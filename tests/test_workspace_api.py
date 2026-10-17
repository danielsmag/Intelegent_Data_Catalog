"""Workspace membership, the shared workspace, and space visibility."""

import asyncio
from pathlib import Path
from uuid import UUID

import httpx
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import AppSettings, AuthSettings, load_settings
from intelegent_data_catalog.exceptions import AuthenticationError
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database

_ADMIN: str = "admin-token"
_ANALYST: str = "analyst-token"


class _Tokens:
    """Map bearer tokens to principals. Tests do not call Keycloak."""

    def __init__(self, principals: dict[str, Principal]) -> None:
        self._principals: dict[str, Principal] = principals

    def verify(self, token: str) -> Principal:
        """Return the principal for a known token."""
        found: Principal | None = self._principals.get(token)
        if found is None:
            raise AuthenticationError("invalid token")
        return found


def test_shared_workspace_cannot_be_deleted(tmp_path: Path) -> None:
    """The seeded Shared workspace rejects delete."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _open_app(tmp_path)
    try:
        listed: httpx.Response = _send(app, "GET", "/api/v1/workspaces")
        assert listed.status_code == 200
        shared_id: str = _shared_id(_list(listed.json()))
        removed: httpx.Response = _send(app, "DELETE", f"/api/v1/workspaces/{shared_id}")
        assert removed.status_code == 409
    finally:
        database.dispose()


def test_isolated_space_is_visible_only_to_members(tmp_path: Path) -> None:
    """OPEN spaces are global. ISOLATED spaces follow the active workspace."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _auth_app(tmp_path)
    try:
        created: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/workspaces",
            {"name": "Finance"},
            token=_ADMIN,
        )
        assert created.status_code == 201
        workspace_id: str = _text(_object(created), "id")
        private: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/spaces",
            {"name": "Ledger", "isolation_mode": "ISOLATED"},
            token=_ADMIN,
            workspace_id=workspace_id,
        )
        assert private.status_code == 201
        space_id: str = _text(_object(private), "id")
        opened: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/spaces",
            {"name": "Public"},
            token=_ADMIN,
        )
        assert opened.status_code == 201
        signed_in: httpx.Response = _send(app, "GET", "/api/v1/me", token=_ANALYST)
        assert signed_in.status_code == 200
        hidden: httpx.Response = _send(app, "GET", "/api/v1/spaces", token=_ANALYST)
        assert hidden.status_code == 200
        assert _names(_list(hidden.json())) == ["Public"]
        missing: httpx.Response = _send(app, "GET", f"/api/v1/spaces/{space_id}", token=_ANALYST)
        assert missing.status_code == 404
        refused: httpx.Response = _send(
            app,
            "GET",
            "/api/v1/spaces",
            token=_ANALYST,
            workspace_id=workspace_id,
        )
        assert refused.status_code == 403
        added: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace_id}/members",
            {"email": "analyst@example.com", "role": "MEMBER"},
            token=_ADMIN,
        )
        assert added.status_code == 201
        visible: httpx.Response = _send(
            app,
            "GET",
            "/api/v1/spaces",
            token=_ANALYST,
            workspace_id=workspace_id,
        )
        assert visible.status_code == 200
        assert "Ledger" in _names(_list(visible.json()))
        assert "Public" in _names(_list(visible.json()))
        scope: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/spaces/{space_id}/scopes",
            {"name": "Books"},
            token=_ANALYST,
            workspace_id=workspace_id,
        )
        assert scope.status_code == 201
        scope_id: str = _text(_object(scope), "id")
        blocked_scope: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/scopes/{scope_id}",
            token=_ANALYST,
        )
        assert blocked_scope.status_code == 404
        blocked_create: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/workspaces",
            {"name": "Other"},
            token=_ANALYST,
        )
        assert blocked_create.status_code == 403
        rebuild: httpx.Response = _send(app, "POST", "/api/v1/graph/rebuild", token=_ANALYST)
        assert rebuild.status_code == 403
    finally:
        database.dispose()


def test_search_hides_isolated_tables(tmp_path: Path) -> None:
    """Search drops tables that sit in a space the caller cannot see."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _auth_app(tmp_path)
    try:
        workspace: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/workspaces",
            {"name": "Finance"},
            token=_ADMIN,
        )
        workspace_id: str = _text(_object(workspace), "id")
        space: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/spaces",
            {"name": "Ledger", "isolation_mode": "ISOLATED"},
            token=_ADMIN,
            workspace_id=workspace_id,
        )
        space_id: str = _text(_object(space), "id")
        scope: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/spaces/{space_id}/scopes",
            {"name": "Books"},
            token=_ADMIN,
            workspace_id=workspace_id,
        )
        scope_id: str = _text(_object(scope), "id")
        source: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/data-sources",
            {"name": "Lake", "type": "ICEBERG_REST", "configuration": {}},
            token=_ADMIN,
        )
        source_id: str = _text(_object(source), "id")
        table: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/scopes/{scope_id}/tables",
            {
                "data_source_id": source_id,
                "name": "secret_ledger",
                "table_type": "TABLE",
                "physical_catalog": "catalog",
                "physical_namespace": "sales",
                "physical_table": "secret_ledger",
                "source_ref": {
                    "catalog": "catalog",
                    "namespace": "sales",
                    "table": "secret_ledger",
                },
            },
            token=_ADMIN,
            workspace_id=workspace_id,
        )
        assert table.status_code == 201
        _send(app, "GET", "/api/v1/me", token=_ANALYST)
        hidden: httpx.Response = _send(
            app,
            "GET",
            "/api/v1/search?q=secret_ledger",
            token=_ANALYST,
        )
        assert hidden.status_code == 200
        assert hidden.json()["hits"] == []
        shown: httpx.Response = _send(
            app,
            "GET",
            "/api/v1/search?q=secret_ledger",
            token=_ADMIN,
            workspace_id=workspace_id,
        )
        assert shown.status_code == 200
        hits: object = _object(shown)["hits"]
        assert isinstance(hits, list)
        assert len(hits) == 1
    finally:
        database.dispose()


def _auth_app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    current: AppSettings = load_settings()
    settings: AppSettings = current.model_copy(
        update={
            "auth": AuthSettings(
                enabled=True,
                issuer="http://issuer",
                audience="catalog-ui",
                jwks_url="http://jwks",
            )
        }
    )
    verifier: _Tokens = _Tokens(
        {
            _ADMIN: Principal(
                subject="admin",
                email="admin@example.com",
                name="Admin",
                roles=frozenset({"catalog-admin"}),
                is_admin=True,
            ),
            _ANALYST: Principal(
                subject="analyst",
                email="analyst@example.com",
                name="Analyst",
                roles=frozenset(),
                is_admin=False,
            ),
        }
    )
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.settings.override(providers.Object(settings))
    container.oidc_verifier.override(providers.Object(verifier))
    return create_app(container), database


def _open_app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase]:
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
    *,
    token: str | None = None,
    workspace_id: str | None = None,
) -> httpx.Response:
    return asyncio.run(_request(app, method, path, body, token, workspace_id))


def _shared_id(rows: list[object]) -> str:
    item: object
    for item in rows:
        assert isinstance(item, dict)
        if item.get("is_system") is True:
            value: object = item["id"]
            assert isinstance(value, str)
            UUID(value)
            return value
    raise AssertionError("shared workspace missing")


def _names(rows: list[object]) -> list[str]:
    found: list[str] = []
    item: object
    for item in rows:
        assert isinstance(item, dict)
        name: object = item["name"]
        assert isinstance(name, str)
        found.append(name)
    return found


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def _object(response: httpx.Response) -> dict[str, object]:
    payload: object = response.json()
    assert isinstance(payload, dict)
    return payload


def _text(body: dict[str, object], key: str) -> str:
    value: object = body[key]
    assert isinstance(value, str)
    return value


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    body: dict[str, object] | None,
    token: str | None,
    workspace_id: str | None,
) -> httpx.Response:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if workspace_id is not None:
        headers["X-Workspace-Id"] = workspace_id
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, json=body, headers=headers)
