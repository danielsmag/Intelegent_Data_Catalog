"""Notebook kernel routes: access checks, gateway env, and channel tickets."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import (
    AppSettings,
    AuthSettings,
    CredentialEntry,
    NotebookSettings,
    load_settings,
)
from intelegent_data_catalog.exceptions import AuthenticationError
from intelegent_data_catalog.notebooks.gateway import KernelGatewayClient
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import DataSourceType, EngineType
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import (
    DataSourceRow,
    EngineConnectionRow,
    SourceEngineBindingRow,
)
from intelegent_data_catalog.storage.object_store import MemoryObjectStore

_PASSWORD: str = "s3cr3t-reader"
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


def test_kernel_start_sends_env_and_hides_secrets(tmp_path: Path) -> None:
    """The gateway receives JDBC passwords. The browser response does not."""
    captured: list[dict[str, object]] = []
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path, enabled=True, gateway=_gateway(captured))
    workspace: str = str(SHARED_WORKSPACE_ID)
    try:
        _seed_sales(database)
        folder_id: str = _shared_folder(app, workspace)
        created: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/files/text",
            {"parent_id": folder_id, "name": "notes.ipynb", "text": "{}"},
        )
        assert created.status_code == 201
        node_id: str = _text(_object(created), "id")
        started: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/{node_id}/kernel",
        )
        assert started.status_code == 201
        body: str = started.text
        assert _PASSWORD not in body
        payload: dict[str, object] = _object(started)
        assert payload["spark_catalogs"] == ["appdb"]
        assert captured[0]["name"] == "pyspark"
        env: object = captured[0]["env"]
        assert isinstance(env, dict)
        catalogs: object = env["CATALOG_SPARK_CATALOGS_JSON"]
        assert isinstance(catalogs, str)
        assert _PASSWORD in catalogs
        kernel_id: str = _text(payload, "kernel_id")
        stopped: httpx.Response = _send(app, "DELETE", f"/api/v1/kernels/{kernel_id}")
        assert stopped.status_code == 204
    finally:
        database.dispose()


def test_folder_and_sql_are_not_notebooks(tmp_path: Path) -> None:
    """Kernels only start for notebook files the caller can see."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path, enabled=True, gateway=_gateway([]))
    workspace: str = str(SHARED_WORKSPACE_ID)
    try:
        roots: httpx.Response = _send(app, "GET", f"/api/v1/workspaces/{workspace}/nodes")
        shared_id: str = _id_named(_list(roots), "Shared")
        folder: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/folders",
            {"parent_id": shared_id, "name": "notes"},
        )
        folder_id: str = _text(_object(folder), "id")
        missing_folder: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/{folder_id}/kernel",
        )
        assert missing_folder.status_code == 404
        sql: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/files/text",
            {"parent_id": folder_id, "name": "spend.sql", "text": "select 1"},
        )
        sql_id: str = _text(_object(sql), "id")
        missing_sql: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/{sql_id}/kernel",
        )
        assert missing_sql.status_code == 404
    finally:
        database.dispose()


def test_private_notebook_is_hidden(tmp_path: Path) -> None:
    """Another user cannot start a kernel for a notebook in a private folder."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _auth_app(tmp_path)
    workspace: str = str(SHARED_WORKSPACE_ID)
    try:
        roots: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/workspaces/{workspace}/nodes",
            token=_ALICE,
        )
        users_id: str = _id_named(_list(roots), "Users")
        home: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/workspaces/{workspace}/nodes?parent_id={users_id}",
            token=_ALICE,
        )
        home_id: str = _id_named(_list(home), "alice")
        created: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/files/text",
            {"parent_id": home_id, "name": "notes.ipynb", "text": "{}"},
            token=_ALICE,
        )
        node_id: str = _text(_object(created), "id")
        hidden: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/{node_id}/kernel",
            token=_BOB,
        )
        assert hidden.status_code == 404
    finally:
        database.dispose()


def test_disabled_notebooks_are_not_implemented(tmp_path: Path) -> None:
    """A visible notebook still refuses to start when kernels are off."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path, enabled=False, gateway=_gateway([]))
    workspace: str = str(SHARED_WORKSPACE_ID)
    try:
        folder_id: str = _shared_folder(app, workspace)
        created: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/files/text",
            {"parent_id": folder_id, "name": "notes.ipynb", "text": "{}"},
        )
        node_id: str = _text(_object(created), "id")
        refused: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/workspaces/{workspace}/nodes/{node_id}/kernel",
        )
        assert refused.status_code == 501
    finally:
        database.dispose()


def test_channel_rejects_a_bad_ticket(tmp_path: Path) -> None:
    """The socket closes before any kernel frame when the ticket is not signed."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path, enabled=True, gateway=_gateway([]))
    try:
        path: str = "/api/v1/kernels/missing/channels?ticket=nope"
        with (
            TestClient(app) as client,
            pytest.raises(WebSocketDisconnect) as caught,
        ):
            socket = client.websocket_connect(path)
            with socket:
                socket.receive_text()
        assert caught.value.code == 4401
    finally:
        database.dispose()


def _gateway(captured: list[dict[str, object]]) -> KernelGatewayClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/kernels":
            body: object = json.loads(request.content.decode("utf-8"))
            assert isinstance(body, dict)
            captured.append(body)
            return httpx.Response(201, json={"id": "kernel-1", "name": "pyspark"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(404, json={"message": "missing"})
        return httpx.Response(204)

    client: httpx.Client = httpx.Client(transport=httpx.MockTransport(handler))
    return KernelGatewayClient(client, "http://gateway", "token", "pyspark")


def _app(
    tmp_path: Path,
    enabled: bool,
    gateway: KernelGatewayClient,
) -> tuple[FastAPI, CatalogDatabase]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    store: MemoryObjectStore = MemoryObjectStore()
    settings: AppSettings = load_settings().model_copy(
        update={
            "notebook": NotebookSettings(enabled=enabled, ticket_secret=SecretStr("test-secret")),
            "credentials": {
                "source_db": CredentialEntry(user="sales_reader", password=SecretStr(_PASSWORD))
            },
        }
    )
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.settings.override(providers.Object(settings))
    container.object_store.override(providers.Object(store))
    container.kernel_gateway.override(providers.Object(gateway))
    return create_app(container), database


def _auth_app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase]:
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path, enabled=True, gateway=_gateway([]))
    settings: AppSettings = load_settings().model_copy(
        update={
            "auth": AuthSettings(
                enabled=True,
                issuer="http://issuer",
                audience="catalog-ui",
                jwks_url="http://jwks",
            ),
            "notebook": NotebookSettings(enabled=True, ticket_secret=SecretStr("test-secret")),
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
    return app, database


def _seed_sales(database: CatalogDatabase) -> None:
    session = database.session()
    try:
        engine: EngineConnectionRow = EngineConnectionRow(
            id=uuid4(),
            name="warehouse",
            engine_type=EngineType.TRINO.value,
            endpoint="http://trino:8080",
            credential_ref=None,
            configuration={},
            status="ACTIVE",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        source: DataSourceRow = DataSourceRow(
            id=uuid4(),
            name="sales",
            type=DataSourceType.POSTGRES.value,
            configuration={
                "host": "postgres",
                "port": 5432,
                "database": "sales",
                "username": "sales_reader",
            },
            credential_ref="source_db",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(engine)
        session.add(source)
        session.flush()
        session.add(
            SourceEngineBindingRow(
                id=uuid4(),
                data_source_id=source.id,
                engine_connection_id=engine.id,
                binding_key="appdb",
                engine_type=engine.engine_type,
                configuration={},
                default_scope_id=None,
                capabilities={},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()
    finally:
        session.close()


def _shared_folder(app: FastAPI, workspace: str) -> str:
    roots: httpx.Response = _send(app, "GET", f"/api/v1/workspaces/{workspace}/nodes")
    shared_id: str = _id_named(_list(roots), "Shared")
    folder: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/workspaces/{workspace}/nodes/folders",
        {"parent_id": shared_id, "name": "notes"},
    )
    assert folder.status_code == 201
    return _text(_object(folder), "id")


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
