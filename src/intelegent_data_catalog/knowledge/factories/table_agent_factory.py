"""Create a table agent for one catalog id. The factory keeps no per-table cache."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.knowledge.agents.table_agent import TableAgent
from intelegent_data_catalog.knowledge.context.builder import TableContextBuilder
from intelegent_data_catalog.knowledge.context.catalog import (
    CatalogContextProvider,
    PostgresCatalogContextProvider,
)
from intelegent_data_catalog.knowledge.context.graph import (
    GraphContextProvider,
    ProjectionGraphContextProvider,
    RelationshipGraphReader,
)
from intelegent_data_catalog.knowledge.context.lineage import (
    LineageContextProvider,
    MarquezLineageContextProvider,
    PostgresDatasetCatalog,
)
from intelegent_data_catalog.knowledge.models import KnowledgeDefaults
from intelegent_data_catalog.knowledge.profiling.profiler import DataProfiler
from intelegent_data_catalog.lineage.marquez import LineageReader
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
    ) -> None:
        self._defaults: KnowledgeDefaults = defaults or KnowledgeDefaults()
        self._catalog: CatalogContextProvider = catalog
        self._profiler: DataProfiler = profiler
        self._graph: GraphContextProvider = graph
        self._lineage: LineageContextProvider = lineage
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
        )


def build_table_agent_factory(
    session: Session,
    graph: RelationshipGraphReader,
    lineage: LineageReader,
    profiler: DataProfiler,
    defaults: KnowledgeDefaults | None = None,
) -> TableAgentFactory:
    """Wire PostgreSQL, the graph projection, and Marquez for one session.

    ``graph`` is read with ``relationships``. This function does not publish Neo4j.
    """
    repository: RegistryRepository = RegistryRepository(session)
    return TableAgentFactory(
        catalog=PostgresCatalogContextProvider(repository),
        profiler=profiler,
        graph=ProjectionGraphContextProvider(graph),
        lineage=MarquezLineageContextProvider(lineage, PostgresDatasetCatalog(repository)),
        defaults=defaults,
    )
