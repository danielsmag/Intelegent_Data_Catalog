"""Logical manager for one catalog table. Holds no profile cache and no chat history."""

from uuid import UUID

from intelegent_data_catalog.exceptions import (
    GraphUnavailableError,
    LlmUnavailableError,
    ValidationAppError,
)
from intelegent_data_catalog.knowledge.access import (
    accept_column_profile,
    accept_comparison,
    accept_table_profile,
    profiles_physical_data,
    resolve_profile_options,
)
from intelegent_data_catalog.knowledge.context.builder import TableContextBuilder
from intelegent_data_catalog.knowledge.context.catalog import CatalogContextProvider
from intelegent_data_catalog.knowledge.context.column_graph import ColumnGraphAppender
from intelegent_data_catalog.knowledge.context.graph import GraphContextProvider, GraphPublisher
from intelegent_data_catalog.knowledge.context.lineage import LineageContextProvider
from intelegent_data_catalog.knowledge.models import (
    ColumnComparisonProfile,
    ColumnDataProfile,
    ColumnMetadata,
    DataProfileOptions,
    GraphNeighbor,
    GraphPublishResult,
    KnowledgeDefaults,
    LineageContext,
    ProfileOverrides,
    TableAnalysisContext,
    TableCatalogContext,
    TableContextOptions,
    TableDataProfile,
)
from intelegent_data_catalog.knowledge.profiling.profiler import DataProfiler
from intelegent_data_catalog.llm.catalog import TableOperationAgent


class TableAgent:
    """Load metadata, statistics, neighbors, and lineage for one table id.

    Create it through ``TableAgentFactory`` and drop it when the call finishes.
    PostgreSQL keeps the catalog. ``run`` asks the model to carry out one operation.
    """

    def __init__(
        self,
        table_id: UUID,
        catalog: CatalogContextProvider,
        profiler: DataProfiler,
        graph: GraphContextProvider,
        lineage: LineageContextProvider,
        contexts: TableContextBuilder,
        defaults: KnowledgeDefaults | None = None,
        publisher: GraphPublisher | None = None,
        operation_agent: TableOperationAgent | None = None,
        column_graph: ColumnGraphAppender | None = None,
    ) -> None:
        self._table_id: UUID = table_id
        self._catalog: CatalogContextProvider = catalog
        self._profiler: DataProfiler = profiler
        self._graph: GraphContextProvider = graph
        self._lineage: LineageContextProvider = lineage
        self._contexts: TableContextBuilder = contexts
        self._defaults: KnowledgeDefaults = defaults or KnowledgeDefaults()
        self._publisher: GraphPublisher | None = publisher
        self._operation_agent: TableOperationAgent | None = operation_agent
        self._column_graph: ColumnGraphAppender | None = column_graph

    @property
    def table_id(self) -> UUID:
        """Catalog id this agent was created for."""
        return self._table_id

    def get_metadata(self) -> TableCatalogContext:
        """Load the table, columns, keys, relationships, scope, space, and tags."""
        return self._context()

    def get_data_profile(self, options: ProfileOverrides | None = None) -> TableDataProfile:
        """Request table statistics. ``NONE`` and ``METADATA_ONLY`` do not call the profiler."""
        resolved: DataProfileOptions = self._profile_options(options)
        if not profiles_physical_data(resolved.access_mode):
            return TableDataProfile(table_id=self._table_id, access_mode=resolved.access_mode)
        context: TableCatalogContext = self._context()
        raw: TableDataProfile = self._profiler.profile_table(self._table_id, resolved)
        allowed: set[UUID] = {column.id for column in context.columns}
        return accept_table_profile(raw, self._table_id, allowed, resolved)

    def get_column_profile(
        self,
        column_id: UUID,
        options: ProfileOverrides | None = None,
    ) -> ColumnDataProfile:
        """Request statistics for a column on this table."""
        self._require_column(column_id)
        resolved: DataProfileOptions = self._require_physical(options)
        raw: ColumnDataProfile = self._profiler.profile_column(column_id, resolved)
        return accept_column_profile(raw, column_id, resolved)

    def compare_columns(
        self,
        source_column_id: UUID,
        target_column_id: UUID,
        options: ProfileOverrides | None = None,
    ) -> ColumnComparisonProfile:
        """Request overlap ratios. The source column must belong to this table."""
        self._require_column(source_column_id)
        resolved: DataProfileOptions = self._require_physical(options)
        raw: ColumnComparisonProfile = self._profiler.compare_columns(
            source_column_id,
            target_column_id,
            resolved,
        )
        return accept_comparison(raw, source_column_id, target_column_id)

    def get_neighbors(self) -> list[GraphNeighbor]:
        """Load neighboring tables from the graph projection."""
        return self._graph.get_neighbors(self._table_id)

    def get_lineage(self) -> LineageContext:
        """Load Marquez upstream, downstream, and jobs for this table."""
        return self._lineage.get_lineage(self._table_id)

    def build_context(self, options: TableContextOptions | None = None) -> TableAnalysisContext:
        """Combine metadata, an optional profile, neighbors, and lineage.

        When a column-graph appender is configured, the model writes a Neo4j
        subgraph of important column relations for this table.
        """
        built: TableAnalysisContext = self._contexts.build(self._table_id, options)
        if self._column_graph is None:
            return built
        written: TableAnalysisContext = self._column_graph.apply(built)
        if written.table.id != self._table_id:
            raise ValidationAppError("catalog context is for a different table", field="table_id")
        return written

    def publish_graph(self) -> GraphPublishResult:
        """Replace the Neo4j projection. PostgreSQL rows stay unchanged."""
        if self._publisher is None:
            raise GraphUnavailableError("graph projection is not configured")
        written: dict[str, int] = self._publisher.rebuild()
        return GraphPublishResult(nodes=written["nodes"], edges=written["edges"])

    def run(self, instruction: str) -> str:
        """Carry out one catalog operation. The model may read context and publish Neo4j."""
        cleaned: str = instruction.strip()
        if cleaned == "":
            raise ValidationAppError("instruction is empty", field="instruction")
        if self._operation_agent is None:
            raise LlmUnavailableError("language model is not configured")
        return self._operation_agent.run(cleaned, self, self._instructions())

    def _instructions(self) -> str:
        return (
            "You carry out one catalog operation for this table. "
            f"The table id is {self._table_id}. "
            "Read metadata, profiles, neighbors, and lineage before you change anything. "
            "publish_graph replaces the Neo4j projection from PostgreSQL and Marquez. "
            "Do not invent statistics or relationships. "
            "Do not claim the graph was written unless publish_graph returns counts."
        )

    def _context(self) -> TableCatalogContext:
        loaded: TableCatalogContext = self._catalog.get_table_context(self._table_id)
        if loaded.table.id != self._table_id:
            raise ValidationAppError("catalog context is for a different table", field="table_id")
        return loaded

    def _require_column(self, column_id: UUID) -> ColumnMetadata:
        column: ColumnMetadata
        for column in self._context().columns:
            if column.id == column_id:
                return column
        raise ValidationAppError("column does not belong to this table", field="column_id")

    def _profile_options(self, requested: ProfileOverrides | None) -> DataProfileOptions:
        return resolve_profile_options(requested, self._defaults)

    def _require_physical(self, requested: ProfileOverrides | None) -> DataProfileOptions:
        resolved: DataProfileOptions = self._profile_options(requested)
        if not profiles_physical_data(resolved.access_mode):
            raise ValidationAppError(
                "data profiling requires PROFILE_ONLY or SAMPLED",
                field="access_mode",
            )
        return resolved
