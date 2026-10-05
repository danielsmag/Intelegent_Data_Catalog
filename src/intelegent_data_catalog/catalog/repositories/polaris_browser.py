"""Polaris catalog browser used by the explorer."""

from intelegent_data_catalog.catalog.models import (
    CatalogNode,
    ColumnDetail,
    NamespaceNode,
    ObservedColumn,
    ObservedTable,
    QueryResult,
    TableDetail,
    TableSummary,
)
from intelegent_data_catalog.catalog.repositories.polaris import PolarisClient
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.exceptions import TableNotFoundError

_ICEBERG_CONNECTOR: str = "iceberg"


class PolarisCatalogBrowser:
    """Reads Iceberg catalogs from Polaris and samples them through Spark."""

    def __init__(self, client: PolarisClient, query: QueryService) -> None:
        self._client: PolarisClient = client
        self._query: QueryService = query

    def list_tree(self) -> list[CatalogNode]:
        """Return Polaris catalogs, namespaces, and tables in name order."""
        catalogs: list[CatalogNode] = []
        catalog_name: str
        for catalog_name in sorted(self._client.list_catalog_names()):
            namespaces: list[NamespaceNode] = []
            namespace_name: str
            for namespace_name in sorted(self._client.list_namespace_names(catalog_name)):
                table_names: list[str] = self._client.list_table_names(catalog_name, namespace_name)
                tables: list[TableSummary] = [
                    TableSummary(name=table_name) for table_name in sorted(table_names)
                ]
                namespaces.append(NamespaceNode(name=namespace_name, tables=tables))
            catalogs.append(CatalogNode(name=catalog_name, namespaces=namespaces))
        return catalogs

    def load_table(self, catalog: str, namespace: str, table: str) -> TableDetail:
        """Return Iceberg metadata for one table."""
        return self._client.load_table(catalog, namespace, table)

    def list_observed(
        self,
        catalog: str,
        namespace: str,
        tables: list[str] | None,
    ) -> list[ObservedTable]:
        """Load columns for the requested tables, or every table in the namespace."""
        available: set[str] = set(self._client.list_table_names(catalog, namespace))
        selected: list[str] = sorted(available) if tables is None else _unique(tables)
        observed: list[ObservedTable] = []
        table_name: str
        for table_name in selected:
            if table_name not in available:
                raise TableNotFoundError(catalog, namespace, table_name)
            detail: TableDetail = self._client.load_table(catalog, namespace, table_name)
            columns: list[ObservedColumn] = []
            index: int
            column: ColumnDetail
            for index, column in enumerate(detail.columns):
                columns.append(
                    ObservedColumn(
                        name=column.name,
                        type_name=column.type_name,
                        nullable=not column.required,
                        comment=column.comment,
                        ordinal_position=index,
                    )
                )
            observed.append(ObservedTable(name=table_name, kind="TABLE", columns=columns))
        return observed

    def connector_name(self, catalog: str) -> str:
        """Polaris catalogs are Iceberg REST catalogs."""
        del catalog
        return _ICEBERG_CONNECTOR

    def sample(self, catalog: str, namespace: str, table: str, limit: int) -> QueryResult:
        """Return a Spark preview of one Iceberg table."""
        return self._query.sample(catalog, namespace, table, limit)


def _unique(names: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    name: str
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered
