"""HTTP entry for the scope overview graph and scope context builds."""

import asyncio
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import AppSettings, load_settings
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database


def test_scope_graph_returns_tables_and_a_foreign_key(tmp_path: Path) -> None:
    """The overview graph is table nodes plus catalog relationships."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        scope_id: str
        orders_id: str
        customers_id: str
        scope_id, orders_id, customers_id = _scope(app)
        order_column: str = _column(app, orders_id, "customer_id", 0, primary=False)
        customer_column: str = _column(app, customers_id, "id", 0, primary=True)
        created: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/relationships",
            {
                "source_table_id": orders_id,
                "target_table_id": customers_id,
                "relationship_type": "FOREIGN_KEY",
                "origin": "DATABASE_CONSTRAINT",
                "status": "VERIFIED",
                "column_mappings": [
                    {
                        "source_column_id": order_column,
                        "target_column_id": customer_column,
                        "position": 0,
                    }
                ],
            },
        )
        assert created.status_code == 201
        graph: httpx.Response = _send(app, "GET", f"/api/v1/scopes/{scope_id}/graph")
        assert graph.status_code == 200
        body: dict[str, object] = _object(graph)
        nodes: object = body["nodes"]
        edges: object = body["edges"]
        assert isinstance(nodes, list)
        assert isinstance(edges, list)
        assert len(nodes) == 2
        assert len(edges) == 1
        edge: object = edges[0]
        assert isinstance(edge, dict)
        assert edge["kind"] == "REFERENCES"
        assert edge["reason"] == "DATABASE_CONSTRAINT"
        missing: httpx.Response = _send(app, "GET", f"/api/v1/scopes/{uuid4()}/graph")
        assert missing.status_code == 404
    finally:
        database.dispose()


def test_scope_context_is_missing_until_a_build_fails(tmp_path: Path) -> None:
    """A scope with no snapshot is 404. A build against a closed model port fails."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path, closed_model=True)
    try:
        scope_id: str
        scope_id, _, _ = _scope(app)
        missing: httpx.Response = _send(app, "GET", f"/api/v1/scopes/{scope_id}/knowledge-context")
        assert missing.status_code == 404
        started: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/scopes/{scope_id}/knowledge-context",
            {},
        )
        assert started.status_code == 202
        finished: dict[str, object] = _wait(app, _text(_object(started), "id"))
        assert finished["status"] == "failed"
        error: object = finished["error"]
        assert isinstance(error, str)
        assert "language model" in error
    finally:
        database.dispose()


def _scope(app: FastAPI) -> tuple[str, str, str]:
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
    orders_id: str = _catalog_table(app, scope_id, source_id, "orders")
    customers_id: str = _catalog_table(app, scope_id, source_id, "customers")
    return scope_id, orders_id, customers_id


def _catalog_table(app: FastAPI, scope_id: str, source_id: str, name: str) -> str:
    table: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/scopes/{scope_id}/tables",
        {
            "data_source_id": source_id,
            "name": name,
            "table_type": "TABLE",
            "physical_catalog": "polaris",
            "physical_namespace": "sales",
            "physical_table": name,
            "source_ref": {"catalog": "polaris", "namespace": "sales", "table": name},
        },
    )
    assert table.status_code == 201
    return _text(_object(table), "id")


def _column(app: FastAPI, table_id: str, name: str, position: int, *, primary: bool) -> str:
    column: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/tables/{table_id}/columns",
        {
            "name": name,
            "data_type": "bigint",
            "ordinal_position": position,
            "is_primary_key": primary,
        },
    )
    assert column.status_code == 201
    return _text(_object(column), "id")


def _app(tmp_path: Path, *, closed_model: bool = False) -> tuple[FastAPI, CatalogDatabase]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    if closed_model:
        settings: AppSettings = load_settings()
        settings.llm.base_url = "http://127.0.0.1:1"
        settings.llm.timeout_seconds = 1.0
        container.settings.override(providers.Object(settings))
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


def _text(body: dict[str, object], key: str) -> str:
    value: object = body[key]
    assert isinstance(value, str)
    return value


def _wait(app: FastAPI, job_id: str) -> dict[str, object]:
    attempt: int
    for attempt in range(40):
        del attempt
        current: httpx.Response = _send(app, "GET", f"/api/v1/scope-builds/{job_id}")
        assert current.status_code == 200
        body: dict[str, object] = _object(current)
        if body["status"] != "running":
            return body
        time.sleep(0.1)
    raise AssertionError("scope build did not finish")


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
