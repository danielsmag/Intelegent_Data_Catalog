"""HTTP entry for TableAgent.build_context."""

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database


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
        built: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/knowledge-context",
            {},
        )
        assert built.status_code == 200
        body: dict[str, object] = _object(built)
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


def test_build_context_refuses_physical_profile_without_a_profiler(tmp_path: Path) -> None:
    """Asking for a data profile fails until a physical profiler exists."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        table_id: str = _table(app)
        refused: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/knowledge-context",
            {"include_data_profile": True},
        )
        assert refused.status_code == 501
        assert "profiler" in refused.text
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
