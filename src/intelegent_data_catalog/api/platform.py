"""Request-scoped catalog platform. Discovery and query execution stay separate."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.exceptions import TrinoUnavailableError
from intelegent_data_catalog.graph.neo4j_store import Neo4jProjection
from intelegent_data_catalog.graph.service import GraphService
from intelegent_data_catalog.knowledge.context.lineage import EmptyLineageReader
from intelegent_data_catalog.knowledge.factories.table_agent_factory import (
    TableAgentFactory,
    build_table_agent_factory,
)
from intelegent_data_catalog.knowledge.models import TableAnalysisContext, TableContextOptions
from intelegent_data_catalog.knowledge.operations.table_operation import TableAgent
from intelegent_data_catalog.knowledge.profiling.profiler import UnconfiguredDataProfiler
from intelegent_data_catalog.lineage.marquez import LineageReader, MarquezClient
from intelegent_data_catalog.llm.catalog import TableOperationAgent
from intelegent_data_catalog.llm.factory import build_table_operation_agent
from intelegent_data_catalog.registry.annotations import CatalogAnnotations
from intelegent_data_catalog.registry.service import RegistryService
from intelegent_data_catalog.sources.discovery import DiscoveryBatch, DiscoveryRequest
from intelegent_data_catalog.sources.enrichment import PostgresForeignKeyEnricher
from intelegent_data_catalog.sources.sync import MetadataSyncService
from intelegent_data_catalog.sources.trino.client import TrinoClient
from intelegent_data_catalog.sources.trino.discovery import TrinoCatalogSource


class RequestPlatform:
    """Services for one catalog request."""

    def __init__(self, session: Session, settings: AppSettings, query: QueryService) -> None:
        self._session: Session = session
        self.registry: RegistryService = RegistryService(session)
        self.annotations: CatalogAnnotations = CatalogAnnotations(session)
        neo4j: Neo4jProjection | None = None
        if settings.neo4j.enabled:
            neo4j = Neo4jProjection(
                uri=settings.neo4j.uri,
                user=settings.neo4j.user,
                password=settings.neo4j.password,
                database=settings.neo4j.database,
            )
        lineage: MarquezClient | None = None
        if settings.marquez.enabled:
            lineage = MarquezClient(settings.marquez.url, settings.marquez.timeout_seconds)
        self._lineage: LineageReader | None = lineage
        self.graph: GraphService = GraphService(session, lineage, neo4j)
        source: TrinoCatalogSource | _UnconfiguredTrino
        if settings.trino.enabled:
            source = TrinoCatalogSource(
                TrinoClient(
                    host=settings.trino.host,
                    port=settings.trino.port,
                    user=settings.trino.user,
                    timeout_seconds=settings.trino.timeout_seconds,
                )
            )
        else:
            source = _UnconfiguredTrino()
        self.sync: MetadataSyncService = MetadataSyncService(
            session,
            source,
            PostgresForeignKeyEnricher(),
            after_success=self.graph.publish,
        )
        self.query: QueryService = query
        self.sample_limit: int = settings.query.sample_limit
        self._operation_agent: TableOperationAgent = build_table_operation_agent(settings.llm)

    def build_knowledge_context(
        self,
        table_id: UUID,
        options: TableContextOptions,
    ) -> TableAnalysisContext:
        """Build one table context. This does not write Neo4j or call the model."""
        reader: LineageReader = self._lineage if self._lineage is not None else EmptyLineageReader()
        factory: TableAgentFactory = build_table_agent_factory(
            self._session,
            self.graph,
            reader,
            UnconfiguredDataProfiler(),
            publisher=self.graph,
            operation_agent=self._operation_agent,
        )
        agent: TableAgent = factory.create(table_id)
        return agent.build_context(options)


class _UnconfiguredTrino:
    """Discovery stand-in used when Trino is not configured."""

    def discover(self, request: DiscoveryRequest) -> DiscoveryBatch:
        """Fail the scan instead of inventing metadata."""
        del request
        raise TrinoUnavailableError("Trino is not configured")
