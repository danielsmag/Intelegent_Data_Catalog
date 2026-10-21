"""Asset HTTP API: search, membership, configuration, and file promotion."""

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


def test_table_becomes_an_asset_and_can_be_searched_and_detached(tmp_path: Path) -> None:
    """Creating a catalog table stores an asset, a scope membership, and a refresh mode."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        scope_id: str
        table_id: str
        scope_id, table_id = _table(app)
        listed: httpx.Response = _send(app, "GET", f"/api/v1/scopes/{scope_id}/assets")
        assert listed.status_code == 200
        rows: list[object] = listed.json()
        assert isinstance(rows, list)
        assert len(rows) == 1
        asset: dict[str, object] = rows[0]  # type: ignore[assignment]
        assert isinstance(asset, dict)
        assert asset["kind"] == "TABLE"
        asset_id: str = str(asset["id"])
        found: httpx.Response = _send(app, "GET", "/api/v1/assets?q=orders&sort=name&order=asc")
        assert found.status_code == 200
        body: dict[str, object] = found.json()
        assert body["total"] == 1
        config: httpx.Response = _send(app, "PUT", f"/api/v1/assets/{asset_id}/configuration", {
            "interval_seconds": 60,
            "enabled": True,
            "model": "gpt-4.1",
            "effort": "low",
            "refresh_mode": "MANUAL",
            "options": {
                "include_graph": True,
                "include_lineage": True,
                "include_data_profile": False,
            },
        })
        assert config.status_code == 200
        assert config.json()["refresh_mode"] == "MANUAL"
        detached: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/assets/bulk-membership",
            {
                "asset_ids": [asset_id],
                "target_kind": "scope",
                "target_id": scope_id,
                "action": "detach",
            },
        )
        assert detached.status_code == 200
        assert detached.json()["changed"] == 1
        again: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/assets/bulk-membership",
            {
                "asset_ids": [asset_id],
                "target_kind": "scope",
                "target_id": scope_id,
                "action": "detach",
            },
        )
        assert again.json()["unchanged"] == 1
        attached: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/scopes/{scope_id}/assets",
            {"asset_ids": [asset_id]},
        )
        assert attached.status_code == 200
        assert attached.json()["changed"] == 1
        del table_id
    finally:
        database.dispose()


def test_workspace_file_is_a_file_asset(tmp_path: Path) -> None:
    """Saving a workspace file creates a FILE asset in that workspace."""
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
        assert len(items) == 1
        first: object = items[0]
        assert isinstance(first, dict)
        assert first["kind"] == "FILE"
        assert first["name"] == "notes.md"
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
