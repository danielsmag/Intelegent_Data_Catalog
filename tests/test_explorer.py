"""Explorer tree, Trino browsing, and attaching source tables into a scope."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.catalog.models import (
    POLARIS_SOURCE_ID,
    TRINO_SOURCE_ID,
    CatalogNode,
    ExplorerTree,
    NamespaceNode,
    ObservedColumn,
    ObservedTable,
    QueryResult,
    TableDetail,
    TableSummary,
)
from intelegent_data_catalog.catalog.repositories.trino_browser import TrinoCatalogBrowser
from intelegent_data_catalog.catalog.services.explorer_service import ExplorerService
from intelegent_data_catalog.catalog.services.scope_attachment import ScopeAttachmentService
from intelegent_data_catalog.exceptions import (
    PolarisError,
    TableNotFoundError,
    TrinoUnavailableError,
)
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import DataSourceType, MetadataOrigin
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.registry.schemas import ScopeAttachmentCreate, ScopeCreate, SpaceCreate
from tests.registry_factory import OpenRegistry, open_registry


def test_tree_keeps_trino_when_polaris_is_down() -> None:
    """A Polaris outage still returns the Trino source."""
    sales: CatalogNode = CatalogNode(
        name="polaris",
        namespaces=[NamespaceNode(name="sales", tables=[TableSummary(name="customers")])],
    )
    trino: _Browser = _Browser([_customers()], "iceberg", nodes=[sales])
    service: ExplorerService = _service(_DownBrowser(), trino)
    tree: ExplorerTree = service.tree()
    assert tree.sources[0].id == POLARIS_SOURCE_ID
    assert tree.sources[0].available is False
    assert tree.sources[1].id == TRINO_SOURCE_ID
    assert tree.sources[1].available is True
    assert tree.sources[1].catalogs[0].name == "polaris"
    assert tree.sources[1].catalogs[0].namespaces[0].tables[0].name == "customers"


def test_unconfigured_trino_is_listed_as_unavailable() -> None:
    """Trino stays in the tree when discovery is turned off."""
    service: ExplorerService = _service(_Browser([], "iceberg"), None)
    tree: ExplorerTree = service.tree()
    assert tree.sources[1].available is False
    assert tree.sources[1].error == "Trino is not configured"


def test_trino_tree_skips_system_catalogs_and_schemas() -> None:
    """System catalogs and information_schema stay out of the browser."""
    browser: TrinoCatalogBrowser = TrinoCatalogBrowser(_ScriptedTrino(_tree_rows))
    catalogs: list[CatalogNode] = browser.list_tree()
    names: list[str] = [catalog.name for catalog in catalogs]
    assert names == ["appdb", "polaris"]
    polaris: CatalogNode = catalogs[1]
    assert [namespace.name for namespace in polaris.namespaces] == ["sales"]
    assert polaris.namespaces[0].tables[0].name == "customers"


def test_trino_sample_returns_rows() -> None:
    """A sample is a limited select against the quoted Trino path."""
    browser: TrinoCatalogBrowser = TrinoCatalogBrowser(_ScriptedTrino(_sample_rows))
    result: QueryResult = browser.sample("polaris", "sales", "customers", 10)
    assert result.rows == [[1, "Ada"]]
    assert [column.name for column in result.columns] == ["customer_id", "name"]


def test_attach_schema_creates_tables_and_skips_duplicates(tmp_path: Path) -> None:
    """Attaching a schema copies tables once, then reports tables already in the scope."""
    database: CatalogDatabase = _database(tmp_path)
    session = database.session()
    registry: OpenRegistry = open_registry(session)
    try:
        scope_id: UUID = _scope(registry, "Sales")
        service: ScopeAttachmentService = ScopeAttachmentService(
            registry.scopes,
            registry.tables,
            registry.sources,
            _service(_Browser([_customers(), _orders()], "iceberg"), None),
        )
        body: ScopeAttachmentCreate = ScopeAttachmentCreate(
            source_id=POLARIS_SOURCE_ID,
            catalog="catalog",
            namespace="sales",
            tables=None,
        )
        first = service.attach(scope_id, body)
        assert [item.outcome.value for item in first.items] == ["created", "created"]
        columns = registry.tables.list_columns(first.items[0].table_id or scope_id)
        assert columns[0].name == "customer_id"
        assert columns[0].data_type == "long"
        assert columns[0].metadata_origin is MetadataOrigin.NATIVE
        second = service.attach(scope_id, body)
        outcomes: list[str] = [item.outcome.value for item in second.items]
        assert outcomes == ["already_in_scope", "already_in_scope"]
        sources = registry.sources.list_data_sources()
        assert len(sources) == 1
        assert sources[0].type is DataSourceType.ICEBERG_REST
        assert sources[0].name == "Polaris catalog"
    finally:
        session.close()
        database.dispose()


def test_attach_reports_a_table_that_already_belongs_to_another_scope(tmp_path: Path) -> None:
    """A table is not moved when another scope already registered it."""
    database: CatalogDatabase = _database(tmp_path)
    session = database.session()
    registry: OpenRegistry = open_registry(session)
    try:
        first_scope: UUID = _scope(registry, "Sales")
        second_scope: UUID = _scope(registry, "Finance")
        service: ScopeAttachmentService = ScopeAttachmentService(
            registry.scopes,
            registry.tables,
            registry.sources,
            _service(_Browser([_customers()], "iceberg"), None),
        )
        body: ScopeAttachmentCreate = ScopeAttachmentCreate(
            source_id=POLARIS_SOURCE_ID,
            catalog="catalog",
            namespace="sales",
            tables=["customers"],
        )
        service.attach(first_scope, body)
        moved = service.attach(second_scope, body)
        assert moved.items[0].outcome.value == "already_elsewhere"
        assert moved.items[0].scope_id == first_scope
        assert len(registry.tables.list_tables(second_scope)) == 0
    finally:
        session.close()
        database.dispose()


def test_trino_postgres_catalog_uses_a_postgres_data_source(tmp_path: Path) -> None:
    """A PostgreSQL Trino catalog is stored as a Postgres data source."""
    database: CatalogDatabase = _database(tmp_path)
    session = database.session()
    registry: OpenRegistry = open_registry(session)
    try:
        scope_id: UUID = _scope(registry, "App")
        service: ScopeAttachmentService = ScopeAttachmentService(
            registry.scopes,
            registry.tables,
            registry.sources,
            _service(_Browser([], "iceberg"), _Browser([_customers()], "postgresql")),
        )
        result = service.attach(
            scope_id,
            ScopeAttachmentCreate(
                source_id=TRINO_SOURCE_ID,
                catalog="appdb",
                namespace="public",
                tables=["customers"],
            ),
        )
        assert result.items[0].outcome.value == "created"
        stored = registry.tables.get_table(result.items[0].table_id or scope_id)
        assert stored.table_format is None
        assert stored.physical_catalog == "appdb"
        source = registry.sources.list_data_sources()[0]
        assert source.type is DataSourceType.POSTGRES
        assert source.configuration["connector"] == "postgresql"
    finally:
        session.close()
        database.dispose()


def test_missing_table_is_not_attached(tmp_path: Path) -> None:
    """A table name the source does not contain is a 404-class error."""
    database: CatalogDatabase = _database(tmp_path)
    session = database.session()
    registry: OpenRegistry = open_registry(session)
    try:
        scope_id: UUID = _scope(registry, "Sales")
        service: ScopeAttachmentService = ScopeAttachmentService(
            registry.scopes,
            registry.tables,
            registry.sources,
            _service(_Browser([_customers()], "iceberg"), None),
        )
        with pytest.raises(TableNotFoundError):
            service.attach(
                scope_id,
                ScopeAttachmentCreate(
                    source_id=POLARIS_SOURCE_ID,
                    catalog="catalog",
                    namespace="sales",
                    tables=["missing"],
                ),
            )
    finally:
        session.close()
        database.dispose()


def test_explorer_route_returns_sources() -> None:
    """GET /api/v1/explorer/tree returns one node per source."""
    service: ExplorerService = _service(
        _Browser([], "iceberg", nodes=[CatalogNode(name="catalog", namespaces=[])]),
        None,
    )
    container: AppContainer = AppContainer()
    container.explorer_service.override(providers.Object(service))
    app: FastAPI = create_app(container)
    response: httpx.Response = asyncio.run(_request(app, "/api/v1/explorer/tree"))
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    sources: object = body["sources"]
    assert isinstance(sources, list)
    assert len(sources) == 2
    first: object = sources[0]
    assert isinstance(first, dict)
    assert first["id"] == "polaris"
    assert first["catalogs"] == [{"name": "catalog", "namespaces": []}]


class _Browser:
    """In-memory catalog source."""

    def __init__(
        self,
        observed: list[ObservedTable],
        connector: str,
        *,
        nodes: list[CatalogNode] | None = None,
    ) -> None:
        self._observed: list[ObservedTable] = observed
        self._connector: str = connector
        self._nodes: list[CatalogNode] = nodes or []

    def list_tree(self) -> list[CatalogNode]:
        return self._nodes

    def load_table(self, catalog: str, namespace: str, table: str) -> TableDetail:
        raise AssertionError(f"unused load {catalog}.{namespace}.{table}")

    def list_observed(
        self,
        catalog: str,
        namespace: str,
        tables: list[str] | None,
    ) -> list[ObservedTable]:
        known: dict[str, ObservedTable] = {item.name: item for item in self._observed}
        names: list[str] = sorted(known) if tables is None else tables
        found: list[ObservedTable] = []
        name: str
        for name in names:
            item: ObservedTable | None = known.get(name)
            if item is None:
                raise TableNotFoundError(catalog, namespace, name)
            found.append(item)
        return found

    def connector_name(self, catalog: str) -> str:
        del catalog
        return self._connector

    def sample(self, catalog: str, namespace: str, table: str, limit: int) -> QueryResult:
        raise AssertionError(f"unused sample {catalog}.{namespace}.{table} {limit}")


class _DownBrowser:
    """Polaris stand-in that fails while listing."""

    def list_tree(self) -> list[CatalogNode]:
        raise PolarisError("polaris down")

    def load_table(self, catalog: str, namespace: str, table: str) -> TableDetail:
        raise PolarisError(f"{catalog}.{namespace}.{table}")

    def list_observed(
        self,
        catalog: str,
        namespace: str,
        tables: list[str] | None,
    ) -> list[ObservedTable]:
        del catalog, namespace, tables
        raise PolarisError("polaris down")

    def connector_name(self, catalog: str) -> str:
        del catalog
        raise PolarisError("polaris down")

    def sample(self, catalog: str, namespace: str, table: str, limit: int) -> QueryResult:
        del catalog, namespace, table, limit
        raise PolarisError("polaris down")


class _ScriptedTrino:
    """Trino client that answers from a SQL predicate."""

    def __init__(self, rows_for: Callable[[str], list[dict[str, object]]]) -> None:
        self._rows_for: Callable[[str], list[dict[str, object]]] = rows_for

    def query(self, sql: str) -> list[dict[str, object]]:
        return self._rows_for(sql)


def _customers() -> ObservedTable:
    return ObservedTable(
        name="customers",
        kind="TABLE",
        columns=[
            ObservedColumn(
                name="customer_id",
                type_name="long",
                nullable=False,
                comment=None,
                ordinal_position=0,
            )
        ],
    )


def _orders() -> ObservedTable:
    return ObservedTable(name="orders", kind="TABLE", columns=[])


def _service(polaris: _Browser | _DownBrowser, trino: _Browser | None) -> ExplorerService:
    return ExplorerService(
        polaris=polaris,
        trino=trino,
        sample_limit=100,
        max_sample_limit=500,
        logger=logging.getLogger("test.explorer"),
    )


def _tree_rows(sql: str) -> list[dict[str, object]]:
    if sql == "SHOW CATALOGS":
        return [{"catalog": "system"}, {"catalog": "polaris"}, {"catalog": "appdb"}]
    if "SHOW SCHEMAS" in sql and "polaris" in sql:
        return [{"schema": "information_schema"}, {"schema": "sales"}]
    if "SHOW SCHEMAS" in sql:
        return [{"schema": "public"}]
    if "SHOW TABLES" in sql:
        return [{"table": "customers"}]
    raise TrinoUnavailableError(sql)


def _sample_rows(sql: str) -> list[dict[str, object]]:
    if "SHOW TABLES" in sql:
        return [{"table": "customers"}]
    if sql.startswith("SELECT * FROM"):
        return [{"customer_id": 1, "name": "Ada"}]
    raise TrinoUnavailableError(sql)


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _scope(service: OpenRegistry, name: str) -> UUID:
    space_id: UUID = service.spaces.create_space(SpaceCreate(name=name)).id
    return service.scopes.create_scope(space_id, ScopeCreate(name=name)).id


async def _request(app: FastAPI, path: str) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response: httpx.Response = await client.get(path)
        return response
