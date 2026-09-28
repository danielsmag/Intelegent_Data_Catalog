"""Catalog API tests with Polaris HTTP mocked at the client boundary."""

import asyncio
import logging

import httpx
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.app import create_app
from intelegent_data_catalog.api.container import ApiContainer
from intelegent_data_catalog.catalog.polaris import PolarisClient
from intelegent_data_catalog.core.settings import PolarisSettings
from tests.test_polaris_client import _route


def test_health_does_not_call_polaris() -> None:
    """GET /api/health stays up without a Polaris call."""
    app: FastAPI = create_app()
    response: httpx.Response = asyncio.run(_request(app, "/api/health"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_tree_route_returns_sales_tables() -> None:
    """GET /api/tree returns the mocked sales namespace."""
    response: httpx.Response = _get("/api/tree")
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    catalogs: object = body["catalogs"]
    assert isinstance(catalogs, list)
    first: object = catalogs[0]
    assert isinstance(first, dict)
    assert first["name"] == "catalog"
    namespaces: object = first["namespaces"]
    assert isinstance(namespaces, list)
    namespace: object = namespaces[0]
    assert isinstance(namespace, dict)
    tables: object = namespace["tables"]
    assert isinstance(tables, list)
    table: object = tables[0]
    assert isinstance(table, dict)
    assert table["name"] == "customer_spend"


def test_table_route_returns_customer_columns() -> None:
    """GET /api/tables returns customer columns from Iceberg metadata."""
    response: httpx.Response = _get("/api/tables/catalog/sales/customers")
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    assert body["snapshot_id"] == "8123456789012345678"
    columns: object = body["columns"]
    assert isinstance(columns, list)
    assert columns[0] == {
        "name": "customer_id",
        "type": "long",
        "required": True,
        "comment": None,
    }


def test_missing_table_route_returns_404() -> None:
    """A missing table is a 404, not a Polaris outage."""
    response: httpx.Response = _get("/api/tables/catalog/sales/missing")
    assert response.status_code == 404
    body: dict[str, object] = response.json()
    assert body["detail"] == "table not found: catalog.sales.missing"


def _get(path: str) -> httpx.Response:
    http: httpx.Client = httpx.Client(transport=httpx.MockTransport(_route))
    reader: PolarisClient = PolarisClient(
        settings=PolarisSettings(),
        http_client=http,
        logger=logging.getLogger("test.api"),
    )
    container: ApiContainer = ApiContainer()
    container.polaris_client.override(providers.Object(reader))
    app: FastAPI = create_app(container)
    try:
        return asyncio.run(_request(app, path))
    finally:
        http.close()


async def _request(app: FastAPI, path: str) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response: httpx.Response = await client.get(path)
        return response
