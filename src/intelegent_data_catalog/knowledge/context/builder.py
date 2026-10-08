"""Assemble one analysis context from catalog, profile, graph, and lineage."""

import logging
from uuid import UUID

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.access import (
    accept_table_profile,
    profiles_physical_data,
    resolve_profile_options,
)
from intelegent_data_catalog.knowledge.context.catalog import CatalogContextProvider
from intelegent_data_catalog.knowledge.context.graph import GraphContextProvider
from intelegent_data_catalog.knowledge.context.lineage import LineageContextProvider
from intelegent_data_catalog.knowledge.models import (
    DataProfileOptions,
    GraphNeighbor,
    KnowledgeDefaults,
    LineageContext,
    TableAnalysisContext,
    TableCatalogContext,
    TableContextOptions,
    TableDataProfile,
)
from intelegent_data_catalog.knowledge.profiling.profiler import DataProfiler

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")


class TableContextBuilder:
    """Build a table context. Physical reads go through ``DataProfiler`` only."""

    def __init__(
        self,
        catalog: CatalogContextProvider,
        profiler: DataProfiler,
        graph: GraphContextProvider,
        lineage: LineageContextProvider,
        defaults: KnowledgeDefaults | None = None,
    ) -> None:
        self._catalog: CatalogContextProvider = catalog
        self._profiler: DataProfiler = profiler
        self._graph: GraphContextProvider = graph
        self._lineage: LineageContextProvider = lineage
        self._defaults: KnowledgeDefaults = defaults or KnowledgeDefaults()

    def build(
        self,
        table_id: UUID,
        options: TableContextOptions | None = None,
    ) -> TableAnalysisContext:
        """Load the requested slices. Metadata-only options skip the profiler."""
        selected: TableContextOptions = options or TableContextOptions()
        logger.info(
            "build context start table_id=%s profile=%s graph=%s lineage=%s",
            table_id,
            selected.include_data_profile,
            selected.include_graph,
            selected.include_lineage,
        )
        catalog: TableCatalogContext = self._catalog.get_table_context(table_id)
        if catalog.table.id != table_id:
            raise ValidationAppError("catalog context is for a different table", field="table_id")
        profile: TableDataProfile | None = self._profile(table_id, catalog, selected)
        neighbors: list[GraphNeighbor] = (
            self._graph.get_neighbors(table_id) if selected.include_graph else []
        )
        lineage: LineageContext | None = self._lineage_context(table_id, selected)
        logger.info(
            "build context done table_id=%s columns=%s relationships=%s "
            "profile=%s neighbors=%s upstream=%s downstream=%s",
            table_id,
            len(catalog.columns),
            len(catalog.relationships),
            _profile_state(profile),
            len(neighbors) if selected.include_graph else "skipped",
            "skipped" if lineage is None else len(lineage.upstream_datasets),
            "skipped" if lineage is None else len(lineage.downstream_datasets),
        )
        return TableAnalysisContext(
            table=catalog.table,
            columns=catalog.columns,
            data_profile=profile,
            existing_relationships=catalog.relationships,
            graph_neighbors=neighbors,
            lineage=lineage,
        )

    def _profile(
        self,
        table_id: UUID,
        catalog: TableCatalogContext,
        selected: TableContextOptions,
    ) -> TableDataProfile | None:
        profile_options: DataProfileOptions = resolve_profile_options(
            selected.profile,
            self._defaults,
        )
        if not selected.include_data_profile or not profiles_physical_data(
            profile_options.access_mode
        ):
            return None
        raw: TableDataProfile = self._profiler.profile_table(table_id, profile_options)
        allowed: set[UUID] = {column.id for column in catalog.columns}
        return accept_table_profile(raw, table_id, allowed, profile_options)

    def _lineage_context(
        self,
        table_id: UUID,
        selected: TableContextOptions,
    ) -> LineageContext | None:
        if not selected.include_lineage:
            return None
        return self._lineage.get_lineage(table_id)


def _profile_state(profile: TableDataProfile | None) -> str:
    if profile is None:
        return "skipped"
    return "present"
