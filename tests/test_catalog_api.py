"""Catalog API tests with Polaris HTTP mocked at the client boundary."""

import asyncio
import logging

import httpx
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.app import create_app
from intelegent_data_catalog.api.container import ApiContainer
from intelegent_data_catalog.catalog.models import QueryColumn, QueryResult
from intelegent_data_catalog.catalog.polaris import PolarisClient
from intelegent_data_catalog.core.settings import PolarisSettings
from intelegent_data_catalog.exceptions import QueryNotFoundError
from tests.test_polaris_client import _route


def test_health_does_not_call_polaris() -> None:
    """GET /api/health stays up without a Polaris call."""
    app: FastAPI = create_app()
    response: httpx.Response = asyncio.run(_request(app, "GET", "/api/health"))
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


def test_sample_route_asks_spark_for_a_limited_select() -> None:
    """GET sample runs a guarded select through the Spark client."""
    spark: _RecordingSpark = _RecordingSpark([[1]])
    response: httpx.Response = _query_get("/api/tables/catalog/sales/customers/sample", spark)
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    assert body["truncated"] is False
    assert body["rows"] == [[1]]
    assert "catalog.sales.customers" in spark.statements[0]
    assert "LIMIT 100" in spark.statements[0]


def test_query_route_rejects_a_drop_before_spark() -> None:
    """POST /api/query does not send a drop to Spark."""
    spark: _RecordingSpark = _RecordingSpark([])
    response: httpx.Response = _query_post(spark, {"sql": "DROP TABLE sales.customers"})
    assert response.status_code == 400
    assert spark.statements == []


def test_query_route_returns_spark_rows() -> None:
    """POST /api/query returns the Spark frame for a select."""
    spark: _RecordingSpark = _RecordingSpark([["Ada"]])
    response: httpx.Response = _query_post(
        spark,
        {"sql": "SELECT name FROM sales.customers"},
    )
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    assert body["rows"] == [["Ada"]]
    columns: object = body["columns"]
    assert isinstance(columns, list)
    assert columns[0] == {"name": "customer_id", "type": "long"}


def test_query_route_maps_a_missing_spark_table_to_404() -> None:
    """A Spark 404 becomes the API 404 body."""
    spark: _RecordingSpark = _RecordingSpark([])
    spark.error = QueryNotFoundError("table or view not found: sales.missing")
    response: httpx.Response = _query_post(spark, {"sql": "SELECT * FROM sales.missing"})
    assert response.status_code == 404
    body: dict[str, object] = response.json()
    assert body["detail"] == "table or view not found: sales.missing"


class _RecordingSpark:
    """Spark stand-in used by the query routes."""

    def __init__(self, rows: list[list[object]]) -> None:
        self.statements: list[str] = []
        self._rows: list[list[object]] = rows
        self.error: Exception | None = None

    def execute(self, sql: str) -> QueryResult:
        """Record SQL, then return rows or the configured error."""
        self.statements.append(sql)
        if self.error is not None:
            raise self.error
        columns: list[QueryColumn] = [QueryColumn(name="customer_id", type="long")]
        return QueryResult(columns=columns, rows=self._rows, truncated=False)


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
        return asyncio.run(_request(app, "GET", path))
    finally:
        http.close()


def _query_get(path: str, spark: _RecordingSpark) -> httpx.Response:
    container: ApiContainer = ApiContainer()
    container.spark_query_client.override(providers.Object(spark))
    app: FastAPI = create_app(container)
    return asyncio.run(_request(app, "GET", path))


def _query_post(spark: _RecordingSpark, body: dict[str, str]) -> httpx.Response:
    container: ApiContainer = ApiContainer()
    container.spark_query_client.override(providers.Object(spark))
    app: FastAPI = create_app(container)
    return asyncio.run(_request(app, "POST", "/api/query", body))


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    body: dict[str, str] | None = None,
) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response: httpx.Response = await client.request(method, path, json=body)
        return response
