"""Explorer tree and Trino browsing."""

import asyncio
import logging
from collections.abc import Callable

import httpx
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
from intelegent_data_catalog.exceptions import (
    PolarisError,
    TableNotFoundError,
    TrinoUnavailableError,
)


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


def _sample_rows(sql: str) -> list[dict[str, object]]:
    if "SHOW TABLES" in sql:
        return [{"table": "customers"}]
    if sql.startswith("SELECT * FROM"):
        return [{"customer_id": 1, "name": "Ada"}]
    raise TrinoUnavailableError(sql)


async def _request(app: FastAPI, path: str) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response: httpx.Response = await client.get(path)
        return response


