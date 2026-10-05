"""Catalog tree grouped by source. One source can fail without hiding the other."""

import logging

from intelegent_data_catalog.catalog.models import (
    POLARIS_SOURCE_ID,
    TRINO_SOURCE_ID,
    CatalogNode,
    ExplorerSourceNode,
    ExplorerTree,
    ObservedTable,
    QueryResult,
    TableDetail,
)
from intelegent_data_catalog.catalog.repositories.browser import CatalogBrowser
from intelegent_data_catalog.exceptions import (
    PolarisError,
    QueryRejectedError,
    TrinoUnavailableError,
    ValidationAppError,
)

_SOURCE_NAMES: dict[str, str] = {
    POLARIS_SOURCE_ID: "Polaris",
    TRINO_SOURCE_ID: "Trino",
}


class ExplorerService:
    """Reads Polaris and Trino for the catalog browser."""

    def __init__(
        self,
        polaris: CatalogBrowser,
        trino: CatalogBrowser | None,
        sample_limit: int,
        max_sample_limit: int,
        logger: logging.Logger,
    ) -> None:
        self._polaris: CatalogBrowser = polaris
        self._trino: CatalogBrowser | None = trino
        self._sample_limit: int = sample_limit
        self._max_sample_limit: int = max_sample_limit
        self._logger: logging.Logger = logger

    def tree(self) -> ExplorerTree:
        """Return each source and the catalogs, schemas, and tables inside it."""
        sources: list[ExplorerSourceNode] = [
            self._source_node(POLARIS_SOURCE_ID, self._polaris),
            self._trino_node(),
        ]
        return ExplorerTree(sources=sources)

    def table(self, source_id: str, catalog: str, namespace: str, table: str) -> TableDetail:
        """Return metadata for one table in one source."""
        return self._browser(source_id).load_table(catalog, namespace, table)

    def sample(
        self,
        source_id: str,
        catalog: str,
        namespace: str,
        table: str,
        limit: int | None,
    ) -> QueryResult:
        """Return a limited row preview from the source that owns the table."""
        row_limit: int = self._checked_limit(limit)
        return self._browser(source_id).sample(catalog, namespace, table, row_limit)

    def observed(
        self,
        source_id: str,
        catalog: str,
        namespace: str,
        tables: list[str] | None,
    ) -> list[ObservedTable]:
        """Return tables to copy into a scope."""
        return self._browser(source_id).list_observed(catalog, namespace, tables)

    def connector_name(self, source_id: str, catalog: str) -> str:
        """Return the connector used to register a data source for attached tables."""
        return self._browser(source_id).connector_name(catalog)

    def _trino_node(self) -> ExplorerSourceNode:
        if self._trino is None:
            return ExplorerSourceNode(
                id=TRINO_SOURCE_ID,
                name=_SOURCE_NAMES[TRINO_SOURCE_ID],
                available=False,
                error="Trino is not configured",
                catalogs=[],
            )
        return self._source_node(TRINO_SOURCE_ID, self._trino)

    def _source_node(self, source_id: str, browser: CatalogBrowser) -> ExplorerSourceNode:
        name: str = _SOURCE_NAMES[source_id]
        try:
            catalogs: list[CatalogNode] = browser.list_tree()
        except (PolarisError, TrinoUnavailableError) as err:
            self._logger.warning("explorer source %s failed: %s", source_id, err)
            return ExplorerSourceNode(
                id=source_id,
                name=name,
                available=False,
                error=str(err),
                catalogs=[],
            )
        return ExplorerSourceNode(
            id=source_id,
            name=name,
            available=True,
            error=None,
            catalogs=catalogs,
        )

    def _browser(self, source_id: str) -> CatalogBrowser:
        if source_id == POLARIS_SOURCE_ID:
            return self._polaris
        if source_id == TRINO_SOURCE_ID:
            if self._trino is None:
                raise TrinoUnavailableError("Trino is not configured")
            return self._trino
        raise ValidationAppError(f"unknown catalog source: {source_id}", field="source_id")

    def _checked_limit(self, limit: int | None) -> int:
        if limit is None:
            return self._sample_limit
        if limit < 1 or limit > self._max_sample_limit:
            raise QueryRejectedError(
                f"sample limit must be between 1 and {self._max_sample_limit}"
            )
        return limit
