"""Asset HTTP API: search, membership, and configuration."""

import asyncio
from pathlib import Path

import httpx
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database


def test_table_and_manual_create_do_not_add_an_asset(tmp_path: Path) -> None:
    """A catalog table stays a table. Only an operator declaration creates an asset."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        scope_id: str
        scope_id, _table_id = _table(app)
        listed: httpx.Response = _send(app, "GET", f"/api/v1/scopes/{scope_id}/assets")
        assert listed.status_code == 200
        assert listed.json() == []
        found: httpx.Response = _send(app, "GET", "/api/v1/assets?q=orders&sort=name&order=asc")
        assert found.status_code == 200
        body: dict[str, object] = found.json()
        assert body["total"] == 0
    finally:
        database.dispose()


def test_workspace_file_is_not_an_asset(tmp_path: Path) -> None:
    """Saving a workspace file does not create a FILE asset."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    workspace: str = str(SHARED_WORKSPACE_ID)
    try:
        roots: httpx.Response = _send(app, "GET", f"/api/v1/workspaces/{workspace}/nodes")
        assert roots.status_code == 200
        shared: str = ""
        item: object
        for item in roots.json():
            assert isinstance(item, dict)
            if item.get("name") == "Shared":
                shared = str(item["id"])
        created: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/files/text",
            {"parent_id": shared, "name": "notes.md", "text": "# Hello"},
        )
        assert created.status_code == 201
        found: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/assets?workspace_id={workspace}&kinds=FILE&sort=name",
        )
        assert found.status_code == 200
        payload: dict[str, object] = found.json()
        items: object = payload["items"]
        assert isinstance(items, list)
        assert items == []
    finally:
        database.dispose()


def _table(app: FastAPI) -> tuple[str, str]:
    space: httpx.Response = _send(app, "POST", "/api/v1/spaces", {"name": "Commerce"})
    assert space.status_code == 201
    space_id: str = str(space.json()["id"])
    scope: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/spaces/{space_id}/scopes",
        {"name": "Sales"},
    )
    assert scope.status_code == 201
    scope_id: str = str(scope.json()["id"])
    source: httpx.Response = _send(
        app,
        "POST",
        "/api/v1/data-sources",
        {"name": "pg", "type": "POSTGRES", "configuration": {}},
    )
    assert source.status_code == 201
    table: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/scopes/{scope_id}/tables",
        {
            "data_source_id": source.json()["id"],
            "name": "orders",
            "table_type": "TABLE",
            "physical_catalog": "db",
            "physical_namespace": "public",
            "physical_table": "orders",
            "source_ref": {"catalog": "db", "namespace": "public", "table": "orders"},
            "source_key": "db.public.orders",
        },
    )
    assert table.status_code == 201
    return scope_id, str(table.json()["id"])


def _app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    return create_app(container), database


def _send(app: FastAPI, method: str, path: str, body: object | None = None) -> httpx.Response:
    return asyncio.run(_request(app, method, path, body))


async def _request(app: FastAPI, method: str, path: str, body: object | None) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=body)
