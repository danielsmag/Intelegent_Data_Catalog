"""Discovery source contract. Trino implements it. Tests use a fake."""

from typing import Protocol

from intelegent_data_catalog.persistence.models import DataSourceRow
from intelegent_data_catalog.sources.discovery import (
    DiscoveryBatch,
    DiscoveryRequest,
    EnrichmentBatch,
)


class CatalogSource(Protocol):
    """Reads metadata. It does not execute the query editor."""

    def discover(self, request: DiscoveryRequest) -> DiscoveryBatch:
        """Return tables visible through one approved catalog binding."""


class RelationshipEnricher(Protocol):
    """Optional native reader. None means the source could not be asked."""

    def enrich(self, source: DataSourceRow, request: DiscoveryRequest) -> EnrichmentBatch | None:
        """Return native keys and types, or None when enrichment is unavailable."""
