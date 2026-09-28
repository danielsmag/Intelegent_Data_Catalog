"""Polaris client tests with a mocked HTTP transport."""

import logging
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx

from intelegent_data_catalog.catalog.models import CatalogTree, TableDetail
from intelegent_data_catalog.catalog.repositories.polaris import PolarisClient, encode_namespace
from intelegent_data_catalog.catalog.services.catalog_service import CatalogService
from intelegent_data_catalog.core.settings import AppSettings, PolarisSettings, load_settings
from intelegent_data_catalog.exceptions import PolarisError, TableNotFoundError

Handler = Callable[[httpx.Request], httpx.Response]

CUSTOMERS_METADATA: dict[str, object] = {
    "location": "s3://warehouse/sales/customers",
    "last-updated-ms": 1_700_000_000_000,
    "current-schema-id": 0,
    "current-snapshot-id": 8123456789012345678,
    "schemas": [
        {
            "schema-id": 0,
            "fields": [
                {"id": 1, "name": "customer_id", "required": True, "type": "long"},
                {"id": 2, "name": "name", "required": True, "type": "string"},
                {
                    "id": 3,
                    "name": "email",
                    "required": True,
                    "type": "string",
                    "doc": "Contact email",
                },
                {"id": 4, "name": "city", "required": True, "type": "string"},
            ],
        }
    ],
}

ORDERS_METADATA: dict[str, object] = {
    "location": "s3://warehouse/sales/orders",
    "last-updated-ms": 1_700_000_000_000,
    "current-schema-id": 0,
    "current-snapshot-id": 42,
    "schemas": [
        {
            "schema-id": 0,
            "fields": [
                {
                    "id": 1,
                    "name": "amount",
                    "required": True,
                    "type": {"type": "decimal", "precision": 10, "scale": 2},
                }
            ],
        }
    ],
}


def test_settings_include_polaris() -> None:
    """The settings file exposes a Polaris section."""
    settings: AppSettings = load_settings()
    assert settings.polaris.uri != ""
    assert settings.polaris.realm != ""
    assert settings.polaris.catalog != ""
    assert settings.polaris.client_id != ""


def test_encode_namespace_uses_unit_separator() -> None:
    """Nested namespaces use the Iceberg unit separator."""
    encoded: str = encode_namespace("sales.internal")
    assert encoded == "sales%1Finternal"


def test_encode_namespace_rejects_empty_part() -> None:
    """A trailing dot is not a namespace."""
    try:
        encode_namespace("sales.")
    except PolarisError as err:
        assert "invalid namespace" in str(err)
        return
    raise AssertionError("expected PolarisError")


def test_token_request_uses_client_credentials() -> None:
    """The client posts client credentials once and reuses the token."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _route(request)

    with _client(handler) as reader:
        reader.list_catalog_names()
        reader.list_catalog_names()

    token_posts: list[httpx.Request] = [
        item for item in seen if item.url.path.endswith("/oauth/tokens")
    ]
    assert len(token_posts) == 1
    token_post: httpx.Request = token_posts[0]
    assert token_post.method == "POST"
    assert token_post.headers["Polaris-Realm"] == "POLARIS"
    form: dict[str, list[str]] = parse_qs(token_post.content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_id"] == ["root"]
    assert form["client_secret"] == ["s3cr3t"]
    assert form["scope"] == ["PRINCIPAL_ROLE:ALL"]
    catalog_gets: list[httpx.Request] = [
        item for item in seen if item.url.path == "/api/management/v1/catalogs"
    ]
    assert len(catalog_gets) == 2
    catalog_get: httpx.Request
    for catalog_get in catalog_gets:
        assert catalog_get.headers["Authorization"] == "Bearer test-token"
        assert catalog_get.headers["Polaris-Realm"] == "POLARIS"


def test_tree_lists_sales_tables() -> None:
    """The tree contains the seeded sales tables."""
    with _client(_route) as reader:
        service: CatalogService = CatalogService(reader)
        tree: CatalogTree = service.tree()
    assert len(tree.catalogs) == 1
    assert tree.catalogs[0].name == "catalog"
    assert tree.catalogs[0].namespaces[0].name == "sales"
    table_names: list[str] = [table.name for table in tree.catalogs[0].namespaces[0].tables]
    assert table_names == ["customer_spend", "customers", "orders"]


def test_customer_columns_come_from_metadata() -> None:
    """Customer columns and the snapshot id come from inline Iceberg metadata."""
    with _client(_route) as reader:
        detail: TableDetail = reader.load_table("catalog", "sales", "customers")
    assert detail.location == "s3://warehouse/sales/customers"
    assert detail.snapshot_id == "8123456789012345678"
    assert detail.last_updated_ms == 1_700_000_000_000
    assert [(column.name, column.type_name, column.required) for column in detail.columns] == [
        ("customer_id", "long", True),
        ("name", "string", True),
        ("email", "string", True),
        ("city", "string", True),
    ]
    assert detail.columns[2].comment == "Contact email"


def test_decimal_column_type() -> None:
    """Decimal fields keep precision and scale."""
    with _client(_route) as reader:
        detail: TableDetail = reader.load_table("catalog", "sales", "orders")
    assert detail.columns[0].name == "amount"
    assert detail.columns[0].type_name == "decimal(10, 2)"


def test_missing_table_raises() -> None:
    """A 404 from load-table becomes a table-not-found error."""
    with _client(_route) as reader:
        try:
            reader.load_table("catalog", "sales", "missing")
        except TableNotFoundError as err:
            assert err.catalog == "catalog"
            assert err.namespace == "sales"
            assert err.table == "missing"
            return
    raise AssertionError("expected TableNotFoundError")


class _ClientContext:
    """Closes the mocked HTTP client when the reader is done."""

    def __init__(self, handler: Handler) -> None:
        self._http: httpx.Client = httpx.Client(transport=httpx.MockTransport(handler))
        self.reader: PolarisClient = PolarisClient(
            settings=PolarisSettings(),
            http_client=self._http,
            logger=logging.getLogger("test.polaris"),
        )

    def __enter__(self) -> PolarisClient:
        return self.reader

    def __exit__(self, *_exc: object) -> None:
        self._http.close()


def _client(handler: Handler) -> _ClientContext:
    return _ClientContext(handler)


def _route(request: httpx.Request) -> httpx.Response:
    path: str = request.url.path
    if path.endswith("/oauth/tokens"):
        return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
    if path == "/api/management/v1/catalogs":
        return httpx.Response(200, json={"catalogs": [{"name": "catalog"}]})
    if path == "/api/catalog/v1/catalog/namespaces":
        return httpx.Response(200, json={"namespaces": [["sales"]]})
    if path == "/api/catalog/v1/catalog/namespaces/sales/tables":
        return httpx.Response(
            200,
            json={
                "identifiers": [
                    {"namespace": ["sales"], "name": "orders"},
                    {"namespace": ["sales"], "name": "customers"},
                    {"namespace": ["sales"], "name": "customer_spend"},
                ]
            },
        )
    if path.endswith("/tables/customers"):
        return httpx.Response(200, json={"metadata": CUSTOMERS_METADATA})
    if path.endswith("/tables/orders"):
        return httpx.Response(200, json={"metadata": ORDERS_METADATA})
    if path.endswith("/tables/missing"):
        return httpx.Response(404, json={"error": "not found"})
    return httpx.Response(500, text=f"unexpected path {path}")
