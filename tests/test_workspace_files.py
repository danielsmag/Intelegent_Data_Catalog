"""Workspace file tree, privacy, and the in-memory object store."""

import asyncio
from pathlib import Path

import httpx
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import (
    AppSettings,
    AuthSettings,
    ObjectStoreSettings,
    load_settings,
)
from intelegent_data_catalog.exceptions import AuthenticationError
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.storage.object_store import MemoryObjectStore

_ADMIN: str = "admin-token"
_ALICE: str = "alice-token"
_BOB: str = "bob-token"


class _Tokens:
    """Map bearer tokens to principals."""

    def __init__(self, principals: dict[str, Principal]) -> None:
        self._principals: dict[str, Principal] = principals

    def verify(self, token: str) -> Principal:
        """Return the principal for a known token."""
        found: Principal | None = self._principals.get(token)
        if found is None:
            raise AuthenticationError("invalid token")
        return found


def test_folder_file_rename_move_and_delete(tmp_path: Path) -> None:
    """A text file round-trips, moves, and drops its object on delete."""
    app: FastAPI
    database: CatalogDatabase
    store: MemoryObjectStore
    app, database, store = _app(tmp_path, max_upload_bytes=50)
    workspace: str = str(SHARED_WORKSPACE_ID)
    try:
        roots: httpx.Response = _send(app, "GET", f"/api/v1/workspaces/{workspace}/nodes")
        assert roots.status_code == 200
        shared_id: str = _id_named(_list(roots), "Shared")
        folder: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/folders",
            {"parent_id": shared_id, "name": "notes"},
        )
        assert folder.status_code == 201
        folder_id: str = _text(_object(folder), "id")
        created: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/files/text",
            {"parent_id": folder_id, "name": "spend.sql", "text": "select 1"},
        )
        assert created.status_code == 201
        file_id: str = _text(_object(created), "id")
        content: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/workspaces/{workspace}/nodes/{file_id}/content",
        )
        assert content.status_code == 200
        assert content.content == b"select 1"
        renamed: httpx.Response = _send(
            app,
            "PATCH",
            f"/api/v1/workspaces/{workspace}/nodes/{file_id}",
            {"name": "daily.sql"},
        )
        assert renamed.status_code == 200
        assert _text(_object(renamed), "path") == "Shared/notes/daily.sql"
        other: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/folders",
            {"parent_id": shared_id, "name": "archive"},
        )
        archive_id: str = _text(_object(other), "id")
        moved: httpx.Response = _send(
            app,
            "PATCH",
            f"/api/v1/workspaces/{workspace}/nodes/{file_id}",
            {"parent_id": archive_id},
        )
        assert moved.status_code == 200
        assert _text(_object(moved), "path") == "Shared/archive/daily.sql"
        assert len(store._objects) == 1
        removed: httpx.Response = _send(
            app,
            "DELETE",
            f"/api/v1/workspaces/{workspace}/nodes/{folder_id}",
        )
        assert removed.status_code == 204
        kept: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/workspaces/{workspace}/nodes/{file_id}",
        )
        assert kept.status_code == 200
        gone: httpx.Response = _send(
            app,
            "DELETE",
            f"/api/v1/workspaces/{workspace}/nodes/{archive_id}",
        )
        assert gone.status_code == 204
        assert store._objects == {}
    finally:
        database.dispose()


def test_upload_limit_and_private_user_folder(tmp_path: Path) -> None:
    """Uploads over the limit are rejected. Another user cannot read a private file."""
    app: FastAPI
    database: CatalogDatabase
    app, database, _store = _auth_app(tmp_path, max_upload_bytes=8)
    workspace: str = str(SHARED_WORKSPACE_ID)
    try:
        alice_roots: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/workspaces/{workspace}/nodes",
            token=_ALICE,
        )
        users_id: str = _id_named(_list(alice_roots), "Users")
        alice_home: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/workspaces/{workspace}/nodes?parent_id={users_id}",
            token=_ALICE,
        )
        home_id: str = _id_named(_list(alice_home), "alice")
        too_big: httpx.Response = _upload(app, workspace, home_id, b"0123456789", token=_ALICE)
        assert too_big.status_code == 413
        uploaded: httpx.Response = _upload(app, workspace, home_id, b"0123", token=_ALICE)
        assert uploaded.status_code == 201
        file_id: str = _text(_object(uploaded), "id")
        hidden: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/workspaces/{workspace}/nodes/{file_id}",
            token=_BOB,
        )
        assert hidden.status_code == 404
        seen: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/workspaces/{workspace}/nodes/{file_id}/content",
            token=_ADMIN,
        )
        assert seen.status_code == 200
        assert seen.content == b"0123"
    finally:
        database.dispose()


def _app(
    tmp_path: Path,
    max_upload_bytes: int,
) -> tuple[FastAPI, CatalogDatabase, MemoryObjectStore]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    store: MemoryObjectStore = MemoryObjectStore()
    current: AppSettings = load_settings()
    settings: AppSettings = current.model_copy(
        update={"object_store": ObjectStoreSettings(max_upload_bytes=max_upload_bytes)}
    )
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.settings.override(providers.Object(settings))
    container.object_store.override(providers.Object(store))
    return create_app(container), database, store


def _auth_app(
    tmp_path: Path,
    max_upload_bytes: int,
) -> tuple[FastAPI, CatalogDatabase, MemoryObjectStore]:
    app: FastAPI
    database: CatalogDatabase
    store: MemoryObjectStore
    app, database, store = _app(tmp_path, max_upload_bytes)
    current: AppSettings = load_settings()
    settings: AppSettings = current.model_copy(
        update={
            "auth": AuthSettings(
                enabled=True,
                issuer="http://issuer",
                audience="catalog-ui",
                jwks_url="http://jwks",
            ),
            "object_store": ObjectStoreSettings(max_upload_bytes=max_upload_bytes),
        }
    )
    verifier: _Tokens = _Tokens(
        {
            _ADMIN: Principal(
                "admin",
                "admin@example.com",
                "Admin",
                frozenset({"catalog-admin"}),
                True,
            ),
            _ALICE: Principal("alice", "alice@example.com", "Alice", frozenset(), False),
            _BOB: Principal("bob", "bob@example.com", "Bob", frozenset(), False),
        }
    )
    container: AppContainer = app.state.container
    container.settings.override(providers.Object(settings))
    container.oidc_verifier.override(providers.Object(verifier))
    container.object_store.override(providers.Object(store))
    return app, database, store


def _upload(
    app: FastAPI,
    workspace_id: str,
    parent_id: str,
    payload: bytes,
    *,
    token: str,
) -> httpx.Response:
    return asyncio.run(_upload_request(app, workspace_id, parent_id, payload, token))


def _send(
    app: FastAPI,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    *,
    token: str | None = None,
) -> httpx.Response:
    return asyncio.run(_request(app, method, path, body, token))


def _id_named(rows: list[object], name: str) -> str:
    item: object
    for item in rows:
        assert isinstance(item, dict)
        if item.get("name") == name:
            value: object = item["id"]
            assert isinstance(value, str)
            return value
    raise AssertionError(f"{name} missing")


def _list(response: httpx.Response) -> list[object]:
    payload: object = response.json()
    assert isinstance(payload, list)
    return payload


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
) -> httpx.Response:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, json=body, headers=headers)


async def _upload_request(
    app: FastAPI,
    workspace_id: str,
    parent_id: str,
    payload: bytes,
    token: str,
) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            f"/api/v1/workspaces/{workspace_id}/nodes/files",
            data={"parent_id": parent_id},
            files={"file": ("note.txt", payload, "text/plain")},
            headers={"Authorization": f"Bearer {token}"},
        )
