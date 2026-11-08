"""Iceberg profiles: SQL, inferred links, markdown, Neo4j subgraph, and the profile API."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.dependencies import get_asset_service
from intelegent_data_catalog.api.exception_handlers import register_exception_handlers
from intelegent_data_catalog.api.v1.routes import assets as asset_routes
from intelegent_data_catalog.assets.definitions.models import (
    IcebergCatalogSourceRef,
    IcebergTableSourceRef,
)
from intelegent_data_catalog.assets.definitions.resolver import SourceResolver
from intelegent_data_catalog.assets.dsl import (
    Asset,
    IcebergCatalogProfile,
    IcebergTable,
)
from intelegent_data_catalog.assets.dsl.collect import collect_definitions
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.base import OperatorContext
from intelegent_data_catalog.assets.dsl.operators.iceberg_catalog import (
    IcebergCatalogProfile as CatalogProfile,
)
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.assets.profiling.graph import ProfileGraphBuilder
from intelegent_data_catalog.assets.profiling.links import ColumnLinkInferrer
from intelegent_data_catalog.assets.profiling.markdown import ProfileMarkdown
from intelegent_data_catalog.assets.profiling.models import (
    ColumnLinkKind,
    ColumnProfile,
    ProfileDocument,
    ProfileScope,
    TableProfile,
    column_fqn,
)
from intelegent_data_catalog.assets.profiling.profiler import IcebergTableProfiler
from intelegent_data_catalog.assets.profiling.sql import ProfileSql
from intelegent_data_catalog.assets.service import AssetService
from intelegent_data_catalog.catalog.models import QueryColumn, QueryResult
from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    GraphUnavailableError,
    PolarisError,
)
from intelegent_data_catalog.graph.document import GraphView, edge_style
from intelegent_data_catalog.graph.profile_subgraph import (
    ProfileSubgraphWrite,
    profile_graph_view,
    profile_node_from_row,
    profile_statements,
)
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    AssetStatus,
    DefinitionKind,
    DefinitionRunStatus,
    OperatorRunTrigger,
)
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import AssetOutputRow, AssetRow, AssetRunRow
from intelegent_data_catalog.sources.github.models import GitHubRepository
from intelegent_data_catalog.storage.object_store import MemoryObjectStore


def test_profile_sql_adds_typed_statistics() -> None:
    """Numeric, string, and boolean columns get the stats Spark can compute."""
    sql: str = ProfileSql().statement(
        "catalog",
        "sales",
        "orders",
        [("id", "long"), ("name", "string"), ("active", "boolean")],
    )
    assert "id__mean" in sql
    assert "id__stddev" in sql
    assert "name__avg_len" in sql
    assert "name__max_len" in sql
    assert "active__true" in sql
    assert sql.endswith("FROM catalog.sales.orders")


def test_profiler_parses_a_candidate_key() -> None:
    """A complete non-null column whose distinct count matches the rows is a key."""
    profiler: IcebergTableProfiler = IcebergTableProfiler(_Iceberg(), _Query())
    result = profiler.profile(
        IcebergTableSourceRef(catalog="catalog", namespace="sales", table="widget"),
        max_columns=10,
    )
    column: ColumnProfile = result.table.columns[0]
    assert result.table.row_count == 4
    assert column.name == "id"
    assert column.candidate_key is True
    assert column.null_ratio == 0
    assert column.mean == 2.5


def test_infer_links_finds_a_reference_and_a_shared_name() -> None:
    """customer_id points at customers.id. status is only a same-name pair."""
    tables: list[TableProfile] = [_customers(), _orders()]
    links = ColumnLinkInferrer().infer(tables)
    kinds: dict[str, ColumnLinkKind] = {link.source: link.kind for link in links}
    reference: str = column_fqn("catalog", "sales", "orders", "customer_id")
    assert kinds[reference] is ColumnLinkKind.CANDIDATE_REFERENCE
    same_name: bool = any(
        link.kind is ColumnLinkKind.SAME_NAME and link.source.endswith(".status") for link in links
    )
    assert same_name
    markdown: str = ProfileMarkdown().render(
        ProfileDocument(
            scope=ProfileScope.SCHEMA,
            scope_name="catalog.sales",
            tables=tables,
            links=links,
        ),
    )
    assert "Rows: 4" in markdown
    assert "CANDIDATE_REFERENCE" in markdown


def test_profile_statements_replace_one_asset_subgraph() -> None:
    """The Cypher deletes the asset's profile projection and recreates inferred edges."""
    owner: UUID = uuid4()
    document: ProfileDocument = ProfileDocument(
        scope=ProfileScope.SCHEMA,
        scope_name="catalog.sales",
        tables=[_customers(), _orders()],
        links=ColumnLinkInferrer().infer([_customers(), _orders()]),
    )
    payload: ProfileSubgraphWrite = ProfileGraphBuilder().build(owner, document)
    statements: list[tuple[str, dict[str, object]]] = profile_statements(payload)
    assert "DETACH DELETE" in statements[0][0]
    assert statements[0][1]["owner"] == str(owner)
    joined: str = "\n".join(query for query, _params in statements)
    assert "CANDIDATE_REFERENCE" in joined
    assert "HAS_SCHEMA" in joined
    assert edge_style("HAS_SCHEMA") == "hierarchy"
    assert edge_style("CANDIDATE_REFERENCE") == "relationship"
    parsed = profile_node_from_row(
        "catalog.sales",
        "sales",
        ["Schema"],
        {"node_id": "catalog.sales", "name": "sales", "projection": "profile", "table_count": 2},
    )
    assert parsed is not None
    assert parsed.kind == "scope"
    assert parsed.properties == {"table_count": 2}
    view: GraphView = profile_graph_view([parsed], [])
    assert view.nodes[0].properties["table_count"] == 2


def test_catalog_profile_writes_markdown_and_records_a_graph_failure() -> None:
    """A catalog profile stores markdown. A Neo4j failure stays on the document."""
    source: IcebergCatalogSourceRef = IcebergCatalogSourceRef(catalog="catalog")
    context: _Context = _Context(source)
    context.iceberg_reader = _Iceberg()
    context.query_reader = _Query()
    context.graphs = _Graphs()
    CatalogProfile().execute(context)
    assert context.outputs[0][0] == "profile"
    assert "Rows: 4" in context.outputs[0][1]
    assert "Skipped: 1bad" in context.outputs[0][1]
    assert len(context.graphs.documents) == 1
    failed: _Context = _Context(source)
    failed.iceberg_reader = _Iceberg()
    failed.query_reader = _Query()
    failed.graphs = _Graphs(error="down")
    CatalogProfile().execute(failed)
    stored: JsonValue | None = failed.outputs[0][2]
    assert isinstance(stored, dict)
    graph: object = stored.get("graph")
    assert isinstance(graph, dict)
    assert graph["written"] is False
    assert graph["error"] == "down"
    assert "not written" in failed.outputs[0][1]


def test_dsl_rejects_a_catalog_operator_on_a_table() -> None:
    """A table source cannot run a catalog profile."""
    with pytest.raises(AssetDefinitionError, match="does not allow"):
        Asset(
            "orders",
            IcebergTable(catalog="catalog", namespace="sales", table="orders"),
            operators=[IcebergCatalogProfile()],
        )


def test_definition_file_accepts_an_iceberg_catalog(tmp_path: Path) -> None:
    """A catalog declaration stores the iceberg_catalog source."""
    del tmp_path
    source: str = (
        "from intelegent_data_catalog.assets.dsl import "
        "Asset, IcebergCatalog, IcebergCatalogProfile\n"
        "Asset(\n"
        '    "lake",\n'
        '    IcebergCatalog(catalog="catalog", include=["sales"]),\n'
        "    operators=[IcebergCatalogProfile(max_tables=10)],\n"
        ")\n"
    )
    spec = collect_definitions(source, "asset_lake.py", DefinitionKind.ASSET, timeout_seconds=5)
    assert spec.assets[0].source.kind == "iceberg_catalog"


def test_resolver_reports_a_missing_catalog(tmp_path: Path) -> None:
    """Polaris failures leave the catalog source unresolved."""
    database: CatalogDatabase
    session: Session
    database, session = _database(tmp_path)
    try:
        resolver: SourceResolver = SourceResolver(session, _DownIceberg(), _GitHub())
        missing = resolver.resolve(IcebergCatalogSourceRef(catalog="lake"))
        assert missing is not None
        assert "lake" in missing.reason
        found = resolver.resolve(IcebergCatalogSourceRef(catalog="catalog"))
        assert found is None
    finally:
        session.close()
        database.dispose()


def test_profile_api_reads_the_newest_document_and_reports_a_missing_graph(
    tmp_path: Path,
) -> None:
    """GET profile returns the stored document. A disabled graph is 503."""
    database: CatalogDatabase
    session: Session
    database, session = _database(tmp_path)
    store: MemoryObjectStore = MemoryObjectStore()
    try:
        asset_id: UUID = uuid4()
        now: datetime = datetime.now(UTC)
        session.add(
            AssetRow(
                id=asset_id,
                kind="ICEBERG_CATALOG",
                key="lake",
                name="lake",
                description=None,
                source={"kind": "iceberg_catalog", "catalog": "catalog"},
                properties={},
                status=AssetStatus.ACTIVE.value,
                created_at=now,
                updated_at=now,
            ),
        )
        run_id: UUID = uuid4()
        session.add(
            AssetRunRow(
                id=run_id,
                asset_id=asset_id,
                item_id=None,
                status=DefinitionRunStatus.SUCCEEDED.value,
                trigger=OperatorRunTrigger.MANUAL.value,
                force=False,
                try_number=1,
                operator_params={},
                error=None,
                queued_at=now,
                started_at=now,
                finished_at=now,
            ),
        )
        session.flush()
        document: ProfileDocument = ProfileDocument(
            scope=ProfileScope.CATALOG,
            scope_name="catalog",
            tables=[_customers()],
        )
        body: bytes = b"# Profile catalog\n"
        payload: bytes = json.dumps(document.model_dump(mode="json")).encode()
        store.put("profile.md", body, "text/markdown")
        store.put("profile.json", payload, "application/json")
        session.add(
            AssetOutputRow(
                id=uuid4(),
                asset_id=asset_id,
                run_id=run_id,
                task_id="iceberg_catalog_profile",
                name="profile",
                markdown_key="profile.md",
                json_key="profile.json",
                size_bytes=len(body),
                created_at=now,
            ),
        )
        session.commit()
        service: AssetService = AssetService(session, store, _Executor(), None)
        read = service.profile(asset_id)
        assert read.document.scope_name == "catalog"
        assert read.markdown.startswith("# Profile")
        with pytest.raises(GraphUnavailableError, match="not configured"):
            service.profile_graph(asset_id)
        empty: AssetService = AssetService(session, store, _Executor(), _Reader())
        assert empty.profile_graph(asset_id).nodes == []
        with pytest.raises(EntityNotFoundError):
            service.profile(uuid4())
    finally:
        session.close()
        database.dispose()


def test_profile_routes_map_missing_and_unavailable() -> None:
    """The profile routes surface 404 and 503 through the catalog error handlers."""
    app: FastAPI = FastAPI()
    register_exception_handlers(app)
    app.include_router(asset_routes.router)
    asset_id: UUID = uuid4()

    class _Missing:
        def profile(self, requested: UUID) -> None:
            raise EntityNotFoundError("asset profile", str(requested))

        def profile_graph(self, requested: UUID) -> None:
            del requested
            raise GraphUnavailableError("graph projection is not configured")

    app.dependency_overrides[get_asset_service] = lambda: _Missing()
    client: TestClient = TestClient(app)
    missing = client.get(f"/assets/{asset_id}/profile")
    unavailable = client.get(f"/assets/{asset_id}/profile/graph")
    assert missing.status_code == 404
    assert unavailable.status_code == 503


class _Context(OperatorContext):
    """Records outputs and serves one source."""

    def __init__(self, source: object) -> None:
        super().__init__(uuid4(), "demo", logging.getLogger("test.profile"), force=False)
        self._source: object = source
        self.outputs: list[tuple[str, str, JsonValue | None]] = []
        self.iceberg_reader: _Iceberg | None = None
        self.query_reader: _Query | None = None
        self.graphs: _Graphs | None = None

    def source(self) -> object:
        return self._source

    def head_sha(self) -> str | None:
        return None

    def remember_head(self, sha: str) -> None:
        del sha

    def write_output(self, name: str, markdown: str, data: JsonValue | None) -> None:
        self.outputs.append((name, markdown, data))

    def airflow(self) -> object:
        raise AssertionError("airflow")

    def iceberg(self) -> object:
        assert self.iceberg_reader is not None
        return self.iceberg_reader

    def query(self) -> object:
        assert self.query_reader is not None
        return self.query_reader

    def github(self) -> object:
        raise AssertionError("github")

    def profile_graph(self) -> _Graphs | None:
        return self.graphs


class _Iceberg:
    def list_namespaces(self, catalog: str) -> list[str]:
        del catalog
        return ["sales", "1bad"]

    def list_table_names(self, catalog: str, namespace: str) -> list[str]:
        del catalog, namespace
        return ["customers", "orders"]

    def load_table_raw(self, catalog: str, namespace: str, table: str) -> dict[str, JsonValue]:
        del catalog, namespace
        if table == "customers":
            fields: list[dict[str, JsonValue]] = [
                {"name": "id", "type": "long", "required": True},
                {"name": "status", "type": "string", "required": False},
            ]
        elif table == "orders":
            fields = [
                {"name": "customer_id", "type": "long", "required": False},
                {"name": "status", "type": "string", "required": False},
            ]
        else:
            fields = [{"name": "id", "type": "long", "required": True}]
        return {
            "metadata": {
                "location": f"s3://lake/{table}",
                "current-schema-id": 0,
                "schemas": [{"schema-id": 0, "fields": fields}],
            },
        }


class _Query:
    def query(self, sql: str) -> QueryResult:
        if "FROM catalog.sales.customers" in sql:
            return _result(
                [
                    "row_count",
                    "id__nulls",
                    "id__distinct",
                    "id__min",
                    "id__max",
                    "id__mean",
                    "id__stddev",
                ],
                [4, 0, 4, "1", "100", 2.5, 1.1],
            )
        if "FROM catalog.sales.orders" in sql and "customer_id" in sql:
            return _result(
                [
                    "row_count",
                    "customer_id__nulls",
                    "customer_id__distinct",
                    "customer_id__min",
                    "customer_id__max",
                    "status__nulls",
                    "status__distinct",
                    "status__min",
                    "status__max",
                ],
                [3, 0, 2, "1", "40", 0, 2, "a", "b"],
            )
        return _result(
            [
                "row_count",
                "id__nulls",
                "id__distinct",
                "id__min",
                "id__max",
                "id__mean",
                "id__stddev",
            ],
            [4, 0, 4, "1", "4", 2.5, 1.1],
        )


class _DownIceberg(_Iceberg):
    def list_namespaces(self, catalog: str) -> list[str]:
        if catalog == "lake":
            raise PolarisError("polaris catalog lake was not found")
        return ["sales"]


class _GitHub:
    def repo(self, owner: str, name: str) -> GitHubRepository:
        return GitHubRepository(
            full_name=f"{owner}/{name}",
            description=None,
            default_branch="master",
            html_url="https://github.com/acme/demo",
        )


class _Graphs:
    def __init__(self, error: str | None = None) -> None:
        self.error: str | None = error
        self.documents: list[ProfileSubgraphWrite] = []

    def replace(self, document: ProfileSubgraphWrite) -> None:
        if self.error is not None:
            raise GraphUnavailableError(self.error)
        self.documents.append(document)


class _Reader:
    def read_profile(self, owner_asset_id: str) -> GraphView:
        del owner_asset_id
        return GraphView(nodes=[], edges=[], truncated=False)


class _Executor:
    def submit(self, workload: OperatorWorkload) -> None:
        del workload


def _result(names: list[str], values: list[JsonValue]) -> QueryResult:
    columns: list[QueryColumn] = [QueryColumn(name=name, type="string") for name in names]
    return QueryResult(columns=columns, rows=[values], truncated=False)


def _customers() -> TableProfile:
    return TableProfile(
        catalog="catalog",
        namespace="sales",
        table="customers",
        row_count=4,
        columns=[
            ColumnProfile(
                name="id",
                iceberg_type="long",
                null_count=0,
                null_ratio=0,
                distinct_count=4,
                uniqueness_ratio=1,
                minimum="1",
                maximum="100",
                candidate_key=True,
            ),
            ColumnProfile(
                name="status",
                iceberg_type="string",
                null_count=0,
                distinct_count=2,
                minimum="a",
                maximum="b",
            ),
        ],
        sql="SELECT 1",
        profiled_at=datetime.now(UTC),
    )


def _orders() -> TableProfile:
    return TableProfile(
        catalog="catalog",
        namespace="sales",
        table="orders",
        row_count=3,
        columns=[
            ColumnProfile(
                name="customer_id",
                iceberg_type="long",
                null_count=0,
                null_ratio=0,
                distinct_count=2,
                minimum="1",
                maximum="40",
                candidate_key=False,
            ),
            ColumnProfile(
                name="status",
                iceberg_type="string",
                null_count=0,
                distinct_count=2,
                minimum="a",
                maximum="b",
            ),
        ],
        sql="SELECT 1",
        profiled_at=datetime.now(UTC),
    )


def _database(tmp_path: Path) -> tuple[CatalogDatabase, Session]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    return database, database.session()

