"""Deterministic asset operators write markdown and skip an unchanged GitHub head."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.models import (
    GitHubRepoSourceRef,
    IcebergTableSourceRef,
)
from intelegent_data_catalog.assets.definitions.scanner import AssetDefinitionScanner
from intelegent_data_catalog.assets.dsl import (
    AirflowInstance,
    Asset,
    GitHubRepoSnapshot,
    IcebergTableMetadata,
    IcebergTableProfile,
)
from intelegent_data_catalog.assets.dsl.collect import collect_definitions
from intelegent_data_catalog.assets.dsl.errors import (
    AssetDefinitionError,
    AssetDefinitionImportError,
)
from intelegent_data_catalog.assets.dsl.operators.base import OperatorContext
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.catalog.models import QueryColumn, QueryResult
from intelegent_data_catalog.core.settings import AssetDefinitionsSettings, GitHubSettings
from intelegent_data_catalog.exceptions import GitHubNotFoundError
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import AirflowInstanceStatus, DefinitionKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import AirflowInstanceRow, AssetRow
from intelegent_data_catalog.sources.github.client import GitHubClient
from intelegent_data_catalog.sources.github.models import GitHubCommit, GitHubRepository, GitHubTree
from intelegent_data_catalog.storage.object_store import MemoryObjectStore


class _Context(OperatorContext):
    """Records outputs and serves one source."""

    def __init__(self, source: object, *, force: bool = False, head: str | None = None) -> None:
        super().__init__(uuid4(), "demo", logging.getLogger("test.assets"), force=force)
        self._source: object = source
        self._head: str | None = head
        self.outputs: list[tuple[str, str]] = []
        self.github_reader: _GitHub | None = None
        self.iceberg_reader: _Iceberg | None = None
        self.query_reader: _Query | None = None

    def source(self) -> object:
        return self._source

    def head_sha(self) -> str | None:
        return self._head

    def remember_head(self, sha: str) -> None:
        self._head = sha

    def write_output(self, name: str, markdown: str, data: JsonValue | None) -> None:
        del data
        self.outputs.append((name, markdown))

    def airflow(self) -> object:
        raise AssertionError("airflow")

    def iceberg(self) -> object:
        assert self.iceberg_reader is not None
        return self.iceberg_reader

    def query(self) -> object:
        assert self.query_reader is not None
        return self.query_reader

    def github(self) -> object:
        assert self.github_reader is not None
        return self.github_reader

    def profile_graph(self) -> None:
        return None


class _Iceberg:
    def list_namespaces(self, catalog: str) -> list[str]:
        del catalog
        return ["sales"]

    def list_table_names(self, catalog: str, namespace: str) -> list[str]:
        del catalog, namespace
        return ["orders"]

    def load_table_raw(self, catalog: str, namespace: str, table: str) -> dict[str, JsonValue]:
        del catalog, namespace
        return {
            "metadata": {
                "location": f"s3://lake/{table}",
                "current-schema-id": 0,
                "schemas": [
                    {
                        "schema-id": 0,
                        "fields": [{"name": "id", "type": "long", "required": True}],
                    },
                ],
            },
        }


class _Query:
    def query(self, sql: str) -> QueryResult:
        del sql
        return QueryResult(
            columns=[
                QueryColumn(name="row_count", type="long"),
                QueryColumn(name="id__nulls", type="long"),
                QueryColumn(name="id__distinct", type="long"),
                QueryColumn(name="id__min", type="string"),
                QueryColumn(name="id__max", type="string"),
            ],
            rows=[[3, 0, 3, "1", "9"]],
            truncated=False,
        )


class _GitHub:
    def __init__(self, sha: str) -> None:
        self.sha: str = sha

    def repo(self, owner: str, name: str) -> GitHubRepository:
        return GitHubRepository(
            full_name=f"{owner}/{name}",
            description="demo",
            default_branch="master",
            html_url="https://github.com/acme/demo",
        )

    def branch_head(self, owner: str, name: str, branch: str) -> GitHubCommit:
        del owner, name, branch
        return GitHubCommit(sha=self.sha, message="ship it", author="ada")

    def tree(self, owner: str, name: str, sha: str) -> GitHubTree:
        del owner, name, sha
        return GitHubTree(sha=self.sha, entries=[])

    def file_text(self, owner: str, name: str, path: str, ref: str) -> str | None:
        del owner, name, path, ref
        return None


def test_collect_rejects_a_mismatched_operator() -> None:
    """An Airflow source cannot run an Iceberg operator."""
    with pytest.raises(AssetDefinitionError, match="does not allow"):
        Asset("prod", AirflowInstance(instance="prod-airflow"), operators=[IcebergTableMetadata()])


def test_collect_parses_a_schedule() -> None:
    """A five-field cron is stored. Anything else fails the file."""
    source: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, IcebergTable, IcebergTableMetadata\n"
        "Asset(\n"
        '    "orders",\n'
        '    IcebergTable(catalog="catalog", namespace="sales", table="orders"),\n'
        "    operators=[IcebergTableMetadata()],\n"
        '    schedule="0 3 * * *",\n'
        ")\n"
    )
    spec = collect_definitions(source, "asset_orders.py", DefinitionKind.ASSET, timeout_seconds=5)
    assert spec.assets[0].schedule == "0 3 * * *"
    bad: str = source.replace("0 3 * * *", "daily")
    with pytest.raises(AssetDefinitionImportError, match="five-field"):
        collect_definitions(bad, "asset_bad.py", DefinitionKind.ASSET, timeout_seconds=5)


def test_iceberg_metadata_and_profile_write_markdown() -> None:
    """Table metadata and profile operators store markdown."""
    source: IcebergTableSourceRef = IcebergTableSourceRef(
        catalog="catalog",
        namespace="sales",
        table="orders",
    )
    context: _Context = _Context(source)
    context.iceberg_reader = _Iceberg()
    context.query_reader = _Query()
    IcebergTableMetadata().execute(context)
    IcebergTableProfile().execute(context)
    names: list[str] = [item[0] for item in context.outputs]
    assert names == ["metadata", "profile"]
    assert "orders" in context.outputs[0][1]
    assert "Rows: 3" in context.outputs[1][1]


def test_github_snapshot_skips_an_unchanged_head() -> None:
    """The same commit does not write markdown unless the run is forced."""
    source: GitHubRepoSourceRef = GitHubRepoSourceRef(owner="acme", repo="demo", branch="master")
    context: _Context = _Context(source, head="abc")
    context.github_reader = _GitHub("abc")
    GitHubRepoSnapshot().execute(context)
    assert context.outputs == []
    forced: _Context = _Context(source, force=True, head="abc")
    forced.github_reader = _GitHub("abc")
    GitHubRepoSnapshot().execute(forced)
    assert forced.outputs[0][0] == "repo"
    assert "abc" in forced.outputs[0][1]


def test_github_client_reads_a_repository() -> None:
    """The client parses a repository payload and raises on 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/demo":
            return httpx.Response(
                200,
                json={
                    "full_name": "acme/demo",
                    "description": "demo",
                    "default_branch": "master",
                    "private": False,
                    "html_url": "https://github.com/acme/demo",
                    "stargazers_count": 2,
                    "language": "Python",
                },
            )
        return httpx.Response(404, json={"message": "missing"})

    client: GitHubClient = GitHubClient(
        GitHubSettings(),
        httpx.Client(transport=httpx.MockTransport(handler)),
        logging.getLogger("test.github"),
    )
    assert client.repo("acme", "demo").full_name == "acme/demo"
    with pytest.raises(GitHubNotFoundError):
        client.repo("acme", "missing")


class _Executor:
    def __init__(self) -> None:
        self.workloads: list[OperatorWorkload] = []

    def submit(self, workload: OperatorWorkload) -> None:
        self.workloads.append(workload)


def test_scan_applies_an_airflow_asset_and_queues_it(tmp_path: Path) -> None:
    """A changed asset file creates the row and queues its operators."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    session: Session = database.session()
    now: datetime = datetime.now(UTC)
    session.add(
        AirflowInstanceRow(
            id=uuid4(),
            name="Prod",
            slug="prod-airflow",
            base_url="http://airflow",
            username="airflow",
            password="secret",
            verify_tls=False,
            status=AirflowInstanceStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
        ),
    )
    session.commit()
    store: MemoryObjectStore = MemoryObjectStore()
    script: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, AirflowInstance, AirflowMetadata\n"
        "Asset(\n"
        '    "prod",\n'
        '    AirflowInstance(instance="prod-airflow"),\n'
        "    operators=[AirflowMetadata()],\n"
        '    schedule="0 * * * *",\n'
        ")\n"
    )
    store.put("asset-definitions/assets/asset_prod.py", script.encode(), "text/x-python")
    executor: _Executor = _Executor()
    scanner: AssetDefinitionScanner = AssetDefinitionScanner(
        session,
        store,
        AssetDefinitionsSettings(enabled=True),
        logging.getLogger("test.assets"),
        executor,
        _Iceberg(),
        _GitHub("abc"),
    )
    try:
        scanner.scan(now)
        asset: AssetRow | None = session.scalar(select(AssetRow).where(AssetRow.key == "prod"))
        assert asset is not None
        assert asset.kind == "AIRFLOW_INSTANCE"
        assert len(executor.workloads) == 1
        assert executor.workloads[0].force is True
        assert executor.workloads[0].trigger.value == "apply"
    finally:
        session.close()
        database.dispose()
