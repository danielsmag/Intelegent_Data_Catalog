"""Create a table agent for one catalog id. The factory keeps no per-table cache."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.knowledge.context.builder import TableContextBuilder
from intelegent_data_catalog.knowledge.context.catalog import (
    CatalogContextProvider,
    PostgresCatalogContextProvider,
)
from intelegent_data_catalog.knowledge.context.column_graph import ColumnGraphAppender
from intelegent_data_catalog.knowledge.context.graph import (
    GraphContextProvider,
    GraphPublisher,
    ProjectionGraphContextProvider,
    RelationshipGraphReader,
)
from intelegent_data_catalog.knowledge.context.lineage import (
    LineageContextProvider,
    MarquezLineageContextProvider,
    PostgresDatasetCatalog,
)
from intelegent_data_catalog.knowledge.models import KnowledgeDefaults
from intelegent_data_catalog.knowledge.operations.table_operation import (
    SavedTableContextReader,
    TableAgent,
    TableContextWriter,
    TableSubgraphReader,
)
from intelegent_data_catalog.knowledge.profiling.profiler import DataProfiler
from intelegent_data_catalog.knowledge.store import KnowledgeContextStore, SemanticContextStore
from intelegent_data_catalog.lineage.marquez import LineageReader
from intelegent_data_catalog.llm.agent import TableModelAgent
from intelegent_data_catalog.registry.repository import RegistryRepository


class TableAgentFactory:
    """Inject shared providers. ``create`` returns a new agent every time."""

    def __init__(
        self,
        catalog: CatalogContextProvider,
        profiler: DataProfiler,
        graph: GraphContextProvider,
        lineage: LineageContextProvider,
        defaults: KnowledgeDefaults | None = None,
        publisher: GraphPublisher | None = None,
        operation_agent: TableModelAgent | None = None,
        column_graph: ColumnGraphAppender | None = None,
        saved: SavedTableContextReader | None = None,
        context_writer: TableContextWriter | None = None,
        subgraph: TableSubgraphReader | None = None,
    ) -> None:
        self._defaults: KnowledgeDefaults = defaults or KnowledgeDefaults()
        self._catalog: CatalogContextProvider = catalog
        self._profiler: DataProfiler = profiler
        self._graph: GraphContextProvider = graph
        self._lineage: LineageContextProvider = lineage
        self._publisher: GraphPublisher | None = publisher
        self._operation_agent: TableModelAgent | None = operation_agent
        self._column_graph: ColumnGraphAppender | None = column_graph
        self._saved: SavedTableContextReader | None = saved
        self._context_writer: TableContextWriter | None = context_writer
        self._subgraph: TableSubgraphReader | None = subgraph
        self._contexts: TableContextBuilder = TableContextBuilder(
            catalog=catalog,
            profiler=profiler,
            graph=graph,
            lineage=lineage,
            defaults=self._defaults,
        )

    def create(self, table_id: UUID) -> TableAgent:
        """Return a manager for ``table_id``. Discard it after the calls finish."""
        return TableAgent(
            table_id=table_id,
            catalog=self._catalog,
            profiler=self._profiler,
            graph=self._graph,
            lineage=self._lineage,
            contexts=self._contexts,
            defaults=self._defaults,
            publisher=self._publisher,
            operation_agent=self._operation_agent,
            column_graph=self._column_graph,
            saved=self._saved,
            context_writer=self._context_writer,
            subgraph=self._subgraph,
        )


def build_table_agent_factory(
    session: Session,
    graph: RelationshipGraphReader,
    lineage: LineageReader,
    profiler: DataProfiler,
    defaults: KnowledgeDefaults | None = None,
    publisher: GraphPublisher | None = None,
    operation_agent: TableModelAgent | None = None,
    column_graph: ColumnGraphAppender | None = None,
    subgraph: TableSubgraphReader | None = None,
) -> TableAgentFactory:
    """Wire PostgreSQL, the graph projection, Marquez, and the table model agent.

    ``graph`` is read with ``relationships``. ``publisher`` writes the full Neo4j
    projection when an operation calls ``publish_graph``. ``column_graph`` writes
    a column subgraph when ``build_context`` runs. ``operation_agent`` serves both
    ``run`` and ``describe_*``; without it they fail until a model is configured.
    """
    repository: RegistryRepository = RegistryRepository(session)
    return TableAgentFactory(
        catalog=PostgresCatalogContextProvider(repository),
        profiler=profiler,
        graph=ProjectionGraphContextProvider(graph),
        lineage=MarquezLineageContextProvider(lineage, PostgresDatasetCatalog(repository)),
        defaults=defaults,
        publisher=publisher,
        operation_agent=operation_agent,
        column_graph=column_graph,
        saved=KnowledgeContextStore(session),
        context_writer=SemanticContextStore(session),
        subgraph=subgraph,
    )
