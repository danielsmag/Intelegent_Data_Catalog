"""HTTP CRUD for the application catalog. Spark query routes stay on their own tests."""

import asyncio
from pathlib import Path
from uuid import UUID

import httpx
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database


def test_health_stays_up_with_the_catalog_database(tmp_path: Path) -> None:
    """Existing health route does not require a catalog query."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        response: httpx.Response = _send(app, "GET", "/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        database.dispose()


def test_source_and_table_crud(tmp_path: Path) -> None:
    """Create a data source and table without exposing a source key rewrite."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        source: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/data-sources",
            {
                "name": "Lake",
                "type": "ICEBERG_REST",
                "credential_ref": "vault:prod/snowflake",
                "configuration": {"warehouse": "s3://warehouse"},
            },
        )
        assert source.status_code == 201
        source_body: dict[str, object] = _object(source)
        assert source_body["credential_ref"] == "vault:prod/snowflake"
        assert "password" not in source_body
        secret: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/data-sources",
            {"name": "Secret", "type": "POSTGRES", "configuration": {"password": "hunter2"}},
        )
        assert secret.status_code == 422
        assert "hunter2" not in secret.text
        embedded: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/engine-connections",
            {
                "name": "Trino",
                "engine_type": "TRINO",
                "endpoint": "http://user:pass@trino:8080",
            },
        )
        assert embedded.status_code == 422
        table: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/tables",
            _table_payload(_text(source_body, "id"), "Lakehouse", "Sales", "Orders"),
        )
        assert table.status_code == 201
        table_body: dict[str, object] = _object(table)
        assert table_body["source_key"] == "Lakehouse.Sales.Orders"
        assert table_body["description"] is None
        assert table_body["source_description"] == "imported text"
        patched: httpx.Response = _send(
            app,
            "PATCH",
            f"/api/v1/tables/{_text(table_body, 'id')}",
            {"description": "User orders", "source_key": "rewritten"},
        )
        assert patched.status_code == 422
        described: httpx.Response = _send(
            app,
            "PATCH",
            f"/api/v1/tables/{_text(table_body, 'id')}",
            {"description": "User orders"},
        )
        assert described.status_code == 200
        described_body: dict[str, object] = _object(described)
        assert described_body["description"] == "User orders"
        assert described_body["source_description"] == "imported text"
        assert described_body["source_key"] == "Lakehouse.Sales.Orders"
    finally:
        database.dispose()


def test_bindings_keep_spark_and_trino_addresses_explicit(tmp_path: Path) -> None:
    """A query binding stores the Spark key it was given and cannot borrow Trino."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        source_id: str
        table_id: str
        source_id, table_id = _hierarchy(app)
        trino: dict[str, object] = _object(
            _send(
                app,
                "POST",
                "/api/v1/engine-connections",
                {"name": "Trino", "engine_type": "TRINO", "endpoint": "http://trino:8080"},
            )
        )
        spark: dict[str, object] = _object(
            _send(
                app,
                "POST",
                "/api/v1/engine-connections",
                {"name": "Spark", "engine_type": "SPARK_SQL", "endpoint": "http://spark:8091"},
            )
        )
        discovery_binding: dict[str, object] = _object(
            _send(
                app,
                "POST",
                "/api/v1/source-bindings",
                {
                    "data_source_id": source_id,
                    "engine_connection_id": _text(trino, "id"),
                    "binding_key": "lakehouse",
                },
            )
        )
        query_binding: dict[str, object] = _object(
            _send(
                app,
                "POST",
                "/api/v1/source-bindings",
                {
                    "data_source_id": source_id,
                    "engine_connection_id": _text(spark, "id"),
                    "binding_key": "polaris",
                },
            )
        )
        assert discovery_binding["capabilities"] == {
            "columns": "UNKNOWN",
            "relationships": "UNKNOWN",
            "statistics": "UNKNOWN",
            "constraints": "UNKNOWN",
        }
        discovery: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/bindings",
            _binding_payload(_text(discovery_binding, "id"), "lakehouse.sales.orders", "DISCOVERY"),
        )
        assert discovery.status_code == 201
        query: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/bindings",
            _binding_payload(
                _text(query_binding, "id"),
                "polaris.sales.orders",
                "QUERY",
                selected=True,
            ),
        )
        assert query.status_code == 201
        query_body: dict[str, object] = _object(query)
        assert query_body["engine_ref_key"] == "polaris.sales.orders"
        assert query_body["engine_ref_key"] != "lakehouse.sales.orders"
        mismatched: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/bindings",
            _binding_payload(_text(discovery_binding, "id"), "lakehouse.sales.other", "QUERY"),
        )
        assert mismatched.status_code == 422
        selected_discovery: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/bindings",
            _binding_payload(
                _text(discovery_binding, "id"),
                "lakehouse.sales.other",
                "DISCOVERY",
                selected=True,
            ),
        )
        assert selected_discovery.status_code == 422
        second: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/bindings",
            _binding_payload(
                _text(query_binding, "id"),
                "polaris.sales.orders_v2",
                "QUERY",
                selected=True,
            ),
        )
        assert second.status_code == 409
        column: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/tables/{table_id}/columns",
            {"name": "customer_id", "ordinal_position": 0},
        )
        assert column.status_code == 201
        column_body: dict[str, object] = _object(column)
        assert column_body["nullable"] is None
        assert column_body["is_primary_key"] is None
        missing: httpx.Response = _send(app, "GET", f"/api/v1/data-sources/{UUID(int=4)}")
        assert missing.status_code == 404
    finally:
        database.dispose()


def test_composite_relationship_lists_incoming_and_outgoing(tmp_path: Path) -> None:
    """Composite mappings stay ordered, and a column must belong to its table."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        source_id: str
        orders_id: str
        source_id, orders_id = _hierarchy(app)
        customers: dict[str, object] = _object(
            _send(
                app,
                "POST",
                "/api/v1/tables",
                _table_payload(source_id, "Lakehouse", "Sales", "Customers"),
            )
        )
        customers_id: str = _text(customers, "id")
        order_customer: str = _text(
            _object(
                _send(
                    app,
                    "POST",
                    f"/api/v1/tables/{orders_id}/columns",
                    {"name": "customer_id", "ordinal_position": 1},
                )
            ),
            "id",
        )
        order_region: str = _text(
            _object(
                _send(
                    app,
                    "POST",
                    f"/api/v1/tables/{orders_id}/columns",
                    {"name": "region_id", "ordinal_position": 2},
                )
            ),
            "id",
        )
        customer_id: str = _text(
            _object(
                _send(
                    app,
                    "POST",
                    f"/api/v1/tables/{customers_id}/columns",
                    {"name": "id", "ordinal_position": 0},
                )
            ),
            "id",
        )
        customer_region: str = _text(
            _object(
                _send(
                    app,
                    "POST",
                    f"/api/v1/tables/{customers_id}/columns",
                    {"name": "region_id", "ordinal_position": 1},
                )
            ),
            "id",
        )
        created: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/relationships",
            {
                "source_table_id": orders_id,
                "target_table_id": customers_id,
                "relationship_type": "MANUAL",
                "column_mappings": [
                    {"source_column_id": order_customer, "target_column_id": customer_id},
                    {"source_column_id": order_region, "target_column_id": customer_region},
                ],
            },
        )
        assert created.status_code == 201
        body: dict[str, object] = _object(created)
        assert body["origin"] == "MANUAL"
        mappings: object = body["column_mappings"]
        assert isinstance(mappings, list)
        assert len(mappings) == 2
        first: object = mappings[0]
        assert isinstance(first, dict)
        assert first["position"] == 0
        assert first["source_column_name"] == "customer_id"
        listed: httpx.Response = _send(app, "GET", f"/api/v1/tables/{orders_id}/relationships")
        assert listed.status_code == 200
        graph: dict[str, object] = _object(listed)
        outgoing: object = graph["outgoing"]
        incoming: object = graph["incoming"]
        assert isinstance(outgoing, list)
        assert isinstance(incoming, list)
        assert len(outgoing) == 1
        assert incoming == []
        swapped: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/relationships",
            {
                "source_table_id": orders_id,
                "target_table_id": customers_id,
                "column_mappings": [
                    {"source_column_id": customer_id, "target_column_id": order_customer},
                ],
            },
        )
        assert swapped.status_code == 422
    finally:
        database.dispose()


def _hierarchy(app: FastAPI) -> tuple[str, str]:
    source: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/data-sources",
            {"name": "Lake", "type": "ICEBERG_REST"},
        )
    )
    table: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/tables",
            _table_payload(_text(source, "id"), "Lakehouse", "Sales", "Orders"),
        )
    )
    return _text(source, "id"), _text(table, "id")


def _table_payload(
    data_source_id: str,
    catalog: str,
    namespace: str,
    table: str,
) -> dict[str, object]:
    return {
        "data_source_id": data_source_id,
        "name": table,
        "table_type": "TABLE",
        "physical_catalog": catalog,
        "physical_namespace": namespace,
        "physical_table": table,
        "source_ref": {"catalog": catalog, "namespace": namespace, "table": table},
        "source_description": "imported text",
        "table_format": "ICEBERG",
    }


def _binding_payload(
    source_binding_id: str,
    engine_ref_key: str,
    role: str,
    selected: bool = False,
) -> dict[str, object]:
    catalog: str
    namespace: str
    table: str
    catalog, namespace, table = engine_ref_key.split(".")
    return {
        "source_engine_binding_id": source_binding_id,
        "engine_ref": {"catalog": catalog, "namespace": namespace, "table": table},
        "engine_ref_key": engine_ref_key,
        "role": role,
        "is_selected": selected,
    }


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
