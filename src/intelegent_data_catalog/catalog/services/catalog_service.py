"""Builds explorer responses from a catalog repository."""

from intelegent_data_catalog.catalog.models import (
    CatalogNode,
    CatalogTree,
    NamespaceNode,
    TableDetail,
    TableSummary,
)
from intelegent_data_catalog.catalog.repositories.catalog import CatalogRepository


class CatalogService:
    """Lists the catalog tree and loads one table."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository: CatalogRepository = repository

    def tree(self) -> CatalogTree:
        """Return catalogs, namespaces, and tables in name order."""
        catalogs: list[CatalogNode] = []
        catalog_name: str
        for catalog_name in sorted(self._repository.list_catalog_names()):
            namespaces: list[NamespaceNode] = []
            namespace_name: str
            for namespace_name in sorted(self._repository.list_namespace_names(catalog_name)):
                table_names: list[str] = self._repository.list_table_names(
                    catalog_name, namespace_name
                )
                tables: list[TableSummary] = [
                    TableSummary(name=table_name) for table_name in sorted(table_names)
                ]
                namespaces.append(NamespaceNode(name=namespace_name, tables=tables))
            catalogs.append(CatalogNode(name=catalog_name, namespaces=namespaces))
        return CatalogTree(catalogs=catalogs)

    def table(self, catalog: str, namespace: str, table: str) -> TableDetail:
        """Return metadata for one table."""
        return self._repository.load_table(catalog, namespace, table)
