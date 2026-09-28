"""Catalog metadata source."""

from typing import Protocol

from intelegent_data_catalog.catalog.models import TableDetail


class CatalogRepository(Protocol):
    """Reads catalogs, schemas, and tables."""

    def list_catalog_names(self) -> list[str]:
        """Return catalog names."""

    def list_namespace_names(self, catalog: str) -> list[str]:
        """Return dotted namespace names in one catalog."""

    def list_table_names(self, catalog: str, namespace: str) -> list[str]:
        """Return table names in one namespace."""

    def load_table(self, catalog: str, namespace: str, table: str) -> TableDetail:
        """Return metadata for one table."""
