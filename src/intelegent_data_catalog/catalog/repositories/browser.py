"""Read catalogs, schemas, and tables from one engine."""

from typing import Protocol

from intelegent_data_catalog.catalog.models import (
    CatalogNode,
    ObservedTable,
    QueryResult,
    TableDetail,
)


class CatalogBrowser(Protocol):
    """Lists one source and loads table metadata from it."""

    def list_tree(self) -> list[CatalogNode]:
        """Return catalogs, schemas, and table names."""

    def load_table(self, catalog: str, namespace: str, table: str) -> TableDetail:
        """Return metadata for one table."""

    def list_observed(
        self,
        catalog: str,
        namespace: str,
        tables: list[str] | None,
    ) -> list[ObservedTable]:
        """Return tables in one schema. None means every table in the schema."""

    def connector_name(self, catalog: str) -> str:
        """Return the connector name used to choose a data source type."""

    def sample(self, catalog: str, namespace: str, table: str, limit: int) -> QueryResult:
        """Return a limited row preview."""
