"""HTTP entry for the space overview graph."""

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


def test_space_graph_returns_scopes_tables_and_a_foreign_key(tmp_path: Path) -> None:
    """The overview graph is scope nodes, table nodes, hierarchy, and relationships."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        space_id: str
        orders_id: str
        customers_id: str
        space_id, orders_id, customers_id = _space(app)
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
        graph: httpx.Response = _send(app, "GET", f"/api/v1/spaces/{space_id}/graph")
        assert graph.status_code == 200
        body: dict[str, object] = _object(graph)
        nodes: object = body["nodes"]
        edges: object = body["edges"]
        assert isinstance(nodes, list)
        assert isinstance(edges, list)
        kinds: list[str] = []
        node: object
        for node in nodes:
            assert isinstance(node, dict)
            kind: object = node["kind"]
            assert isinstance(kind, str)
            kinds.append(kind)
        assert kinds.count("scope") == 2
        assert kinds.count("table") == 2
        edge_kinds: list[str] = []
        edge: object
        for edge in edges:
            assert isinstance(edge, dict)
            edge_kind: object = edge["kind"]
            assert isinstance(edge_kind, str)
            edge_kinds.append(edge_kind)
        assert edge_kinds.count("HAS_TABLE") == 2
        assert edge_kinds.count("REFERENCES") == 1
        missing: httpx.Response = _send(app, "GET", f"/api/v1/spaces/{uuid4()}/graph")
        assert missing.status_code == 404
    finally:
        database.dispose()


def _space(app: FastAPI) -> tuple[str, str, str]:
    space: httpx.Response = _send(app, "POST", "/api/v1/spaces", {"name": "Commerce"})
    assert space.status_code == 201
    space_id: str = _text(_object(space), "id")
    ordering: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/spaces/{space_id}/scopes",
        {"name": "Ordering"},
    )
    assert ordering.status_code == 201
    billing: httpx.Response = _send(
        app,
        "POST",
        f"/api/v1/spaces/{space_id}/scopes",
        {"name": "Billing"},
    )
    assert billing.status_code == 201
    source: httpx.Response = _send(
        app,
        "POST",
        "/api/v1/data-sources",
        {"name": "Lake", "type": "ICEBERG_REST"},
    )
    assert source.status_code == 201
    source_id: str = _text(_object(source), "id")
    orders_id: str = _catalog_table(app, _text(_object(ordering), "id"), source_id, "orders")
    customers_id: str = _catalog_table(app, _text(_object(billing), "id"), source_id, "customers")
    return space_id, orders_id, customers_id


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
