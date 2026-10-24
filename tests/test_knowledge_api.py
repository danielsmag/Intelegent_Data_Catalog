"""HTTP entry for TableAgent.build_context."""

import asyncio
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.assets.models import AssetAnalysisContext
from intelegent_data_catalog.assets.store import AssetKnowledgeStore
from intelegent_data_catalog.assets.sync import AssetSync
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.knowledge.context.catalog import PostgresCatalogContextProvider
from intelegent_data_catalog.knowledge.models import TableAnalysisContext, TableCatalogContext
from intelegent_data_catalog.knowledge.store import KnowledgeContextStore
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import AssetKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import CatalogTableRow
from intelegent_data_catalog.registry.repository import RegistryRepository


def test_build_context_returns_catalog_graph_and_lineage(tmp_path: Path) -> None:
    """The table action loads metadata, neighbors, and lineage without a data profile."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        table_id: str = _table(app)
        column: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/columns",
            {
                "name": "id",
                "data_type": "bigint",
                "ordinal_position": 0,
                "is_primary_key": True,
            },
        )
        assert column.status_code == 201
        started: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/knowledge-context",
            {},
        )
        assert started.status_code == 202
        finished: dict[str, object] = _wait_for_build(app, _text(_object(started), "id"))
        assert finished["status"] == "succeeded"
        saved: httpx.Response = _send(app, "GET", f"/api/v1/tables/{table_id}/knowledge-context")
        assert saved.status_code == 200
        body: dict[str, object] = _object(saved)
        table: dict[str, object] = _nested(body, "table")
        assert table["name"] == "Orders"
        space: dict[str, object] = _nested(table, "space")
        scope: dict[str, object] = _nested(table, "scope")
        assert space["name"] == "Commerce"
        assert scope["name"] == "Ordering"
        assert body["data_profile"] is None
        assert body["graph_neighbors"] == []
        lineage: dict[str, object] = _nested(body, "lineage")
        assert lineage["upstream_datasets"] == []
        assert lineage["producer_jobs"] == []
        columns: object = body["columns"]
        assert isinstance(columns, list)
        assert len(columns) == 1
        first: object = columns[0]
        assert isinstance(first, dict)
        assert first["name"] == "id"
        assert first["is_primary_key"] is True
        assert first["is_foreign_key"] is False
        assert body["column_relations"] == []
    finally:
        database.dispose()


def test_build_context_missing_table_is_not_found(tmp_path: Path) -> None:
    """An unknown catalog id does not invent a context."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        missing: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{uuid4()}/knowledge-context",
            {},
        )
        assert missing.status_code == 404
    finally:
        database.dispose()


def test_built_context_is_saved_and_readable(tmp_path: Path) -> None:
    """A later read returns the snapshot written by build, including after a new client."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        table_id: str = _table(app)
        missing: httpx.Response = _send(app, "GET", f"/api/v1/tables/{table_id}/knowledge-context")
        assert missing.status_code == 404
        started: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/knowledge-context",
            {},
        )
        assert started.status_code == 202
        finished: dict[str, object] = _wait_for_build(app, _text(_object(started), "id"))
        assert finished["status"] == "succeeded"
        saved: httpx.Response = _send(app, "GET", f"/api/v1/tables/{table_id}/knowledge-context")
        assert saved.status_code == 200
        assert _nested(_object(saved), "table")["name"] == "Orders"
        again: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/knowledge-context",
            {"include_lineage": False},
        )
        assert again.status_code == 202
        replaced_job: dict[str, object] = _wait_for_build(app, _text(_object(again), "id"))
        assert replaced_job["status"] == "succeeded"
        replaced: httpx.Response = _send(app, "GET", f"/api/v1/tables/{table_id}/knowledge-context")
        assert replaced.status_code == 200
        assert _object(replaced)["lineage"] is None
    finally:
        database.dispose()


def test_asset_backed_table_stores_the_snapshot_on_the_asset(tmp_path: Path) -> None:
    """A linked table refuses the table build. Reads prefer the asset snapshot."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        table_id: str = _table(app)
        asset_id: str = _attach_asset(database, table_id)
        _save_table_snapshot(database, table_id, "table row")
        _save_asset_snapshot(database, table_id, asset_id, {"table_id": table_id, "columns": 0})
        held: httpx.Response = _send(app, "GET", f"/api/v1/tables/{table_id}/knowledge-context")
        assert held.status_code == 200
        assert _nested(_object(held), "table")["description"] == "table row"
        refused: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/knowledge-context",
            {},
        )
        assert refused.status_code == 409
        assert asset_id in _text(_object(refused), "detail")
        _save_asset_snapshot(database, table_id, asset_id, None)
        saved: httpx.Response = _send(app, "GET", f"/api/v1/tables/{table_id}/knowledge-context")
        assert saved.status_code == 200
        assert _nested(_object(saved), "table")["description"] == "asset row"
        asset_saved: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/assets/{asset_id}/knowledge-context",
        )
        assert asset_saved.status_code == 200
        payload: object = _object(asset_saved)["payload"]
        assert isinstance(payload, dict)
        table: object = payload["table"]
        assert isinstance(table, dict)
        assert table["name"] == "Orders"
        assert table["description"] == "asset row"
    finally:
        database.dispose()


def test_build_context_refuses_physical_profile_without_a_profiler(tmp_path: Path) -> None:
    """Asking for a data profile fails until a physical profiler exists."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        table_id: str = _table(app)
        started: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/knowledge-context",
            {"include_data_profile": True},
        )
        assert started.status_code == 202
        failed: dict[str, object] = _wait_for_build(app, _text(_object(started), "id"))
        assert failed["status"] == "failed"
        error: object = failed["error"]
        assert isinstance(error, str)
        assert "profiler" in error
    finally:
        database.dispose()


def test_space_context_is_missing_until_started_and_a_patch_stores_user_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A space with no snapshot is 404. Starting a build returns 202. A patch is USER."""

    def _skip(self: RequestPlatform, space_id: object, options: object) -> None:
        del self, space_id, options

    monkeypatch.setattr(RequestPlatform, "build_space_context", _skip)
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        space: httpx.Response = _send(app, "POST", "/api/v1/spaces", {"name": "Commerce"})
        assert space.status_code == 201
        space_id: str = _text(_object(space), "id")
        missing: httpx.Response = _send(app, "GET", f"/api/v1/spaces/{space_id}/knowledge-context")
        assert missing.status_code == 404
        started: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/spaces/{space_id}/knowledge-context",
            {},
        )
        assert started.status_code == 202
        finished: dict[str, object] = _wait_for_space_build(app, _text(_object(started), "id"))
        assert finished["status"] == "succeeded"
        patched: httpx.Response = _send(
            app,
            "PATCH",
            f"/api/v1/spaces/{space_id}",
            {"context": "Commerce covers sales."},
        )
        assert patched.status_code == 200
        body: dict[str, object] = _object(patched)
        assert body["context"] == "Commerce covers sales."
        assert body["context_origin"] == "USER"
    finally:
        database.dispose()


def test_airflow_context_is_missing_until_started_and_a_patch_stores_user_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An instance with no snapshot is 404. Starting a build returns 202. A patch is USER."""

    def _skip(self: RequestPlatform, instance_id: object, options: object) -> None:
        del self, instance_id, options

    monkeypatch.setattr(RequestPlatform, "build_airflow_context", _skip)
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        created: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/airflow/instances",
            {
                "name": "Local",
                "base_url": "http://airflow.test",
                "username": "admin",
                "password": "s3cret-airflow",
            },
        )
        assert created.status_code == 201
        instance_id: str = _text(_object(created), "id")
        missing: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/airflow/instances/{instance_id}/knowledge-context",
        )
        assert missing.status_code == 404
        started: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/airflow/instances/{instance_id}/knowledge-context",
            {},
        )
        assert started.status_code == 202
        job_id: str = _text(_object(started), "id")
        finished: dict[str, object] = _wait_for_airflow_build(app, job_id)
        assert finished["status"] == "succeeded"
        patched: httpx.Response = _send(
            app,
            "PATCH",
            f"/api/v1/airflow/instances/{instance_id}",
            {"context": "Nightly sales pipelines."},
        )
        assert patched.status_code == 200
        body: dict[str, object] = _object(patched)
        assert body["context"] == "Nightly sales pipelines."
        assert body["context_origin"] == "USER"
    finally:
        database.dispose()


def test_generate_table_context_returns_a_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generate route starts a job. An unknown table is not found."""

    def _skip(
        self: RequestPlatform,
        subject_id: object,
        options: object,
    ) -> None:
        del self, subject_id, options

    monkeypatch.setattr(RequestPlatform, "generate_context", _skip)
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        table_id: str = _table(app)
        missing: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{uuid4()}/context/generate",
        )
        assert missing.status_code == 404
        started: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/context/generate",
        )
        assert started.status_code == 202
        job_id: str = _text(_object(started), "id")
        finished: dict[str, object] = _wait_for_generation(app, job_id)
        assert finished["status"] == "succeeded"
        assert finished["subject_kind"] == "table"
        latest: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/tables/{table_id}/context/generation?lane=table",
        )
        assert latest.status_code == 200
        assert _text(_object(latest), "id") == job_id
    finally:
        database.dispose()


def _table(app: FastAPI) -> str:
    space: httpx.Response = _send(app, "POST", "/api/v1/spaces", {"name": "Commerce"})
    assert space.status_code == 201
    space_id: str = _text(_object(space), "id")
    scope: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/spaces/{space_id}/scopes",
        {"name": "Ordering"},
    )
    assert scope.status_code == 201
    scope_id: str = _text(_object(scope), "id")
    source: httpx.Response = _send(
        app,
        "POST",
        "/api/v1/data-sources",
        {"name": "Lake", "type": "ICEBERG_REST"},
    )
    assert source.status_code == 201
    source_id: str = _text(_object(source), "id")
    table: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/scopes/{scope_id}/tables",
        {
            "data_source_id": source_id,
            "name": "Orders",
            "table_type": "TABLE",
            "physical_catalog": "polaris",
            "physical_namespace": "sales",
            "physical_table": "Orders",
            "source_ref": {"catalog": "polaris", "namespace": "sales", "table": "Orders"},
        },
    )
    assert table.status_code == 201
    return _text(_object(table), "id")


def _app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    return create_app(container), database


def _send(
    app: FastAPI,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> httpx.Response:
    return asyncio.run(_request(app, method, path, body))


def _object(response: httpx.Response) -> dict[str, object]:
    payload: object = response.json()
    assert isinstance(payload, dict)
    return payload


def _nested(body: dict[str, object], key: str) -> dict[str, object]:
    value: object = body[key]
    assert isinstance(value, dict)
    return value


def _wait_for_build(app: FastAPI, job_id: str) -> dict[str, object]:
    """Poll until the background build leaves the running state."""
    attempt: int
    for attempt in range(50):
        del attempt
        current: httpx.Response = _send(app, "GET", f"/api/v1/knowledge-builds/{job_id}")
        assert current.status_code == 200
        body: dict[str, object] = _object(current)
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("knowledge build did not finish")


def _attach_asset(database: CatalogDatabase, table_id: str) -> str:
    session: Session = database.session()
    try:
        row: CatalogTableRow | None = session.get(CatalogTableRow, UUID(table_id))
        assert row is not None
        asset_id: UUID = AssetSync(session).ensure_table(row).id
        session.commit()
        return str(asset_id)
    finally:
        session.close()


def _save_table_snapshot(database: CatalogDatabase, table_id: str, description: str) -> None:
    session: Session = database.session()
    try:
        KnowledgeContextStore(session).save(_catalog_snapshot(session, table_id, description))
    finally:
        session.close()


def _save_asset_snapshot(
    database: CatalogDatabase,
    table_id: str,
    asset_id: str,
    thin: dict[str, object] | None,
) -> None:
    session: Session = database.session()
    try:
        payload: dict[str, object]
        summary: str
        if thin is None:
            built: TableAnalysisContext = _catalog_snapshot(session, table_id, "asset row")
            payload = built.model_dump(mode="json")
            summary = "asset row"
        else:
            payload = thin
            summary = "thin"
        AssetKnowledgeStore(session).save(
            AssetAnalysisContext.model_validate(
                {
                    "asset_id": asset_id,
                    "kind": AssetKind.TABLE.value,
                    "summary": summary,
                    "built_at": utc_now().isoformat(),
                    "payload": payload,
                },
            ),
        )
    finally:
        session.close()


def _catalog_snapshot(session: Session, table_id: str, description: str) -> TableAnalysisContext:
    catalog: TableCatalogContext = PostgresCatalogContextProvider(
        RegistryRepository(session),
    ).get_table_context(UUID(table_id))
    built: TableAnalysisContext = TableAnalysisContext(
        table=catalog.table,
        columns=list(catalog.columns),
        data_profile=None,
        existing_relationships=list(catalog.relationships),
        graph_neighbors=[],
        lineage=None,
    )
    built.table.description = description
    return built


def _wait_for_space_build(app: FastAPI, job_id: str) -> dict[str, object]:
    """Poll until the background space build leaves the running state."""
    attempt: int
    for attempt in range(50):
        del attempt
        current: httpx.Response = _send(app, "GET", f"/api/v1/space-builds/{job_id}")
        assert current.status_code == 200
        body: dict[str, object] = _object(current)
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("space build did not finish")


def _wait_for_airflow_build(app: FastAPI, job_id: str) -> dict[str, object]:
    """Poll until the background Airflow build leaves the running state."""
    attempt: int
    for attempt in range(50):
        del attempt
        current: httpx.Response = _send(app, "GET", f"/api/v1/airflow-builds/{job_id}")
        assert current.status_code == 200
        body: dict[str, object] = _object(current)
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("airflow build did not finish")


def _wait_for_generation(app: FastAPI, job_id: str) -> dict[str, object]:
    """Poll until the background context generation leaves the running state."""
    attempt: int
    for attempt in range(50):
        del attempt
        current: httpx.Response = _send(app, "GET", f"/api/v1/context-generations/{job_id}")
        assert current.status_code == 200
        body: dict[str, object] = _object(current)
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("context generation did not finish")


def _text(body: dict[str, object], key: str) -> str:
    value: object = body[key]
    assert isinstance(value, str)
    return value


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response: httpx.Response = await client.request(method, path, json=body)
        return response
