"""Logical manager for one catalog table. Holds no profile cache and no chat history."""

from contextlib import suppress
from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.exceptions import (
    GraphUnavailableError,
    LlmUnavailableError,
    ValidationAppError,
)
from intelegent_data_catalog.knowledge.access import (
    accept_column_contexts,
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
    ColumnContextInput,
    ColumnContextItem,
    ColumnContextProposal,
    ColumnDataProfile,
    ColumnMetadata,
    ColumnRelation,
    DataProfileOptions,
    GraphNeighbor,
    GraphPublishResult,
    KnowledgeDefaults,
    LineageContext,
    ProfileOverrides,
    TableAnalysisContext,
    TableCatalogContext,
    TableColumnsContextInput,
    TableColumnsContextProposal,
    TableContextInput,
    TableContextOptions,
    TableContextProposal,
    TableDataProfile,
)
from intelegent_data_catalog.knowledge.profiling.profiler import DataProfiler
from intelegent_data_catalog.llm.agent import TableModelAgent
from intelegent_data_catalog.persistence.enums import ContextOrigin

_COLUMN_BATCH: int = 25
_SUBGRAPH_LIMIT: int = 50


class TableSubgraphReader(Protocol):
    """Column links already stored for one table. Neo4j is one implementation."""

    def read(self, table_id: UUID) -> list[ColumnRelation]:
        """Return the knowledge subgraph. An empty list means nothing is stored."""
        ...


class SavedTableContextReader(Protocol):
    """Reads the last table analysis snapshot."""

    def get(self, table_id: UUID) -> TableAnalysisContext | None:
        """Return the snapshot, or none when the table has never been built."""
        ...


class TableContextWriter(Protocol):
    """Stores business context on table and column rows."""

    def save_table(self, table_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the table context."""
        ...

    def save_column(self, column_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the column context."""
        ...


class TableAgent:
    """Load metadata, statistics, neighbors, and lineage for one table id.

    Create it through ``TableAgentFactory`` and drop it when the call finishes.
    PostgreSQL keeps the catalog. ``run`` asks the model to carry out one operation.
    ``describe_*`` ask the same model for business context and store it as ``LLM``.
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
        operation_agent: TableModelAgent | None = None,
        column_graph: ColumnGraphAppender | None = None,
        saved: SavedTableContextReader | None = None,
        context_writer: TableContextWriter | None = None,
        subgraph: TableSubgraphReader | None = None,
    ) -> None:
        self._table_id: UUID = table_id
        self._catalog: CatalogContextProvider = catalog
        self._profiler: DataProfiler = profiler
        self._graph: GraphContextProvider = graph
        self._lineage: LineageContextProvider = lineage
        self._contexts: TableContextBuilder = contexts
        self._defaults: KnowledgeDefaults = defaults or KnowledgeDefaults()
        self._publisher: GraphPublisher | None = publisher
        self._operation_agent: TableModelAgent | None = operation_agent
        self._column_graph: ColumnGraphAppender | None = column_graph
        self._saved: SavedTableContextReader | None = saved
        self._context_writer: TableContextWriter | None = context_writer
        self._subgraph_reader: TableSubgraphReader | None = subgraph

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

    def describe_table(self) -> str:
        """Ask the model for the table's business context and store it."""
        agent: TableModelAgent
        writer: TableContextWriter
        agent, writer = self._describing()
        catalog: TableCatalogContext = self._context()
        proposed: TableContextProposal = agent.describe_table(
            TableContextInput(
                table=catalog,
                saved=self._saved_context(),
                subgraph=self._subgraph(),
            )
        )
        writer.save_table(self._table_id, proposed.context, ContextOrigin.LLM)
        return proposed.context

    def describe_columns(self) -> list[ColumnContextItem]:
        """Ask the model for every column's context, in batches, and store each one."""
        agent: TableModelAgent
        writer: TableContextWriter
        agent, writer = self._describing()
        catalog: TableCatalogContext = self._context()
        saved: TableAnalysisContext | None = self._saved_context()
        subgraph: list[ColumnRelation] = self._subgraph()
        written: list[ColumnContextItem] = []
        start: int = 0
        while start < len(catalog.columns):
            batch: list[ColumnMetadata] = catalog.columns[start : start + _COLUMN_BATCH]
            proposed: TableColumnsContextProposal = agent.describe_columns(
                TableColumnsContextInput(
                    table=catalog,
                    columns=batch,
                    saved=saved,
                    subgraph=subgraph,
                )
            )
            item: ColumnContextItem
            for item in accept_column_contexts(batch, proposed.columns):
                writer.save_column(item.column_id, item.context, ContextOrigin.LLM)
                written.append(item)
            start += _COLUMN_BATCH
        return written

    def describe_column(self, column_id: UUID) -> str:
        """Ask the model for one column's business context and store it."""
        agent: TableModelAgent
        writer: TableContextWriter
        agent, writer = self._describing()
        column: ColumnMetadata = self._require_column(column_id)
        proposed: ColumnContextProposal = agent.describe_column(
            ColumnContextInput(
                table=self._context(),
                column=column,
                saved=self._saved_context(),
                subgraph=self._subgraph(),
            )
        )
        writer.save_column(column_id, proposed.context, ContextOrigin.LLM)
        return proposed.context

    def _describing(self) -> tuple[TableModelAgent, TableContextWriter]:
        if self._operation_agent is None:
            raise LlmUnavailableError("language model is not configured")
        if self._context_writer is None:
            raise LlmUnavailableError("context storage is not configured")
        return self._operation_agent, self._context_writer

    def _saved_context(self) -> TableAnalysisContext | None:
        if self._saved is None:
            return None
        return self._saved.get(self._table_id)

    def _subgraph(self) -> list[ColumnRelation]:
        """Column links for this table. The saved snapshot and Neo4j are merged."""
        collected: list[ColumnRelation] = []
        saved: TableAnalysisContext | None = self._saved_context()
        if saved is not None:
            collected.extend(saved.column_relations)
        if self._subgraph_reader is not None:
            with suppress(GraphUnavailableError):
                collected.extend(self._subgraph_reader.read(self._table_id))
        chosen: list[ColumnRelation] = []
        seen: set[tuple[UUID, UUID, str]] = set()
        relation: ColumnRelation
        for relation in collected:
            key: tuple[UUID, UUID, str] = (
                relation.source_column_id,
                relation.target_column_id,
                relation.kind.value,
            )
            if key in seen:
                continue
            seen.add(key)
            chosen.append(relation)
            if len(chosen) == _SUBGRAPH_LIMIT:
                break
        return chosen

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
