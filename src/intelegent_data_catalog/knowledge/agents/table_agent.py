"""Logical manager for one catalog table. Holds no profile cache and no chat history."""

from uuid import UUID

from intelegent_data_catalog.exceptions import LlmUnavailableError, ValidationAppError
from intelegent_data_catalog.knowledge.access import (
    accept_column_profile,
    accept_comparison,
    accept_table_profile,
    profiles_physical_data,
    resolve_profile_options,
)
from intelegent_data_catalog.knowledge.context.builder import TableContextBuilder
from intelegent_data_catalog.knowledge.context.catalog import CatalogContextProvider
from intelegent_data_catalog.knowledge.context.graph import GraphContextProvider
from intelegent_data_catalog.knowledge.context.lineage import LineageContextProvider
from intelegent_data_catalog.knowledge.models import (
    ColumnComparisonProfile,
    ColumnDataProfile,
    ColumnMetadata,
    DataProfileOptions,
    GraphNeighbor,
    KnowledgeDefaults,
    LineageContext,
    ProfileOverrides,
    TableAnalysisContext,
    TableCatalogContext,
    TableContextOptions,
    TableDataProfile,
)
from intelegent_data_catalog.knowledge.profiling.profiler import DataProfiler
from intelegent_data_catalog.llm.catalog import TableQuestionAgent


class TableAgent:
    """Load metadata, statistics, neighbors, and lineage for one table id.

    Create it through ``TableAgentFactory`` and drop it when the call finishes.
    PostgreSQL keeps the catalog. ``ask`` sends a question to the Pydantic AI agent.
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
        question_agent: TableQuestionAgent | None = None,
    ) -> None:
        self._table_id: UUID = table_id
        self._catalog: CatalogContextProvider = catalog
        self._profiler: DataProfiler = profiler
        self._graph: GraphContextProvider = graph
        self._lineage: LineageContextProvider = lineage
        self._contexts: TableContextBuilder = contexts
        self._defaults: KnowledgeDefaults = defaults or KnowledgeDefaults()
        self._question_agent: TableQuestionAgent | None = question_agent

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
        """Combine metadata, an optional profile, neighbors, and lineage."""
        return self._contexts.build(self._table_id, options)

    def ask(self, question: str) -> str:
        """Answer a question about this table. The model may call catalog tools."""
        cleaned: str = question.strip()
        if cleaned == "":
            raise ValidationAppError("question is empty", field="question")
        if self._question_agent is None:
            raise LlmUnavailableError("language model is not configured")
        return self._question_agent.ask(cleaned, self, self._instructions())

    def _instructions(self) -> str:
        return (
            "You answer questions about one catalog table. "
            f"The table id is {self._table_id}. "
            "Call tools for metadata, profiles, neighbors, and lineage. "
            "Do not invent statistics, keys, or relationships."
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
