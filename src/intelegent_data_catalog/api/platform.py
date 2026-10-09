"""Request-scoped catalog platform. Discovery and query execution stay separate."""

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError, TrinoUnavailableError
from intelegent_data_catalog.graph.neo4j_store import (
    Neo4jColumnSubgraph,
    Neo4jProjection,
    Neo4jScopeSubgraph,
)
from intelegent_data_catalog.graph.service import GraphService
from intelegent_data_catalog.knowledge.context.column_graph import (
    ColumnSubgraphBuilder,
    KnowledgeSubgraphWriter,
    ScopeColumnCatalog,
)
from intelegent_data_catalog.knowledge.context.lineage import EmptyLineageReader
from intelegent_data_catalog.knowledge.context.scope_catalog import (
    MergedTableKnowledgeReader,
    Neo4jTableKnowledgeReader,
    PostgresScopeCatalogContextProvider,
    PostgresTableKnowledgeReader,
    TableKnowledgeReader,
)
from intelegent_data_catalog.knowledge.context.scope_graph import (
    EmptyKnowledgeSubgraphWriter,
    ScopeGraphBuilder,
)
from intelegent_data_catalog.knowledge.factories.scope_agent_factory import (
    ScopeAgentFactory,
    build_scope_agent_factory,
)
from intelegent_data_catalog.knowledge.factories.table_agent_factory import (
    TableAgentFactory,
    build_table_agent_factory,
)
from intelegent_data_catalog.knowledge.models import (
    ScopeAnalysisContext,
    ScopeContextOptions,
    TableAnalysisContext,
    TableContextOptions,
)
from intelegent_data_catalog.knowledge.operations.scope_operation import ScopeAgent
from intelegent_data_catalog.knowledge.operations.table_operation import TableAgent
from intelegent_data_catalog.knowledge.profiling.profiler import UnconfiguredDataProfiler
from intelegent_data_catalog.knowledge.store import (
    KnowledgeContextStore,
    ScopeKnowledgeContextStore,
)
from intelegent_data_catalog.lineage.marquez import LineageReader, MarquezClient
from intelegent_data_catalog.llm.catalog import TableOperationAgent
from intelegent_data_catalog.llm.column_graph import ColumnSubgraphAgent
from intelegent_data_catalog.llm.factory import build_model, build_table_operation_agent
from intelegent_data_catalog.llm.scope_graph import ScopeSubgraphAgent
from intelegent_data_catalog.registry.annotations import CatalogAnnotations
from intelegent_data_catalog.registry.llm_relationships import LlmRelationshipStore
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.service import RegistryService
from intelegent_data_catalog.sources.discovery import DiscoveryBatch, DiscoveryRequest
from intelegent_data_catalog.sources.enrichment import PostgresForeignKeyEnricher
from intelegent_data_catalog.sources.sync import MetadataSyncService
from intelegent_data_catalog.sources.trino.client import TrinoClient
from intelegent_data_catalog.sources.trino.discovery import TrinoCatalogSource

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")


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
        self._neo4j: Neo4jProjection | None = neo4j
        self._column_proposer: ColumnSubgraphAgent | None = None
        if neo4j is not None:
            column_timeout: float = max(settings.llm.timeout_seconds, 600.0)
            self._column_proposer = ColumnSubgraphAgent(
                build_model(settings.llm),
                max_requests=settings.llm.max_tool_steps,
                temperature=settings.llm.temperature,
                max_tokens=settings.llm.max_tokens,
                timeout_seconds=column_timeout,
            )
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
        scope_timeout: float = max(settings.llm.timeout_seconds, 600.0)
        self._scope_proposer: ScopeSubgraphAgent = ScopeSubgraphAgent(
            build_model(settings.llm),
            max_requests=settings.llm.max_tool_steps,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
            timeout_seconds=scope_timeout,
        )

    def build_knowledge_context(
        self,
        table_id: UUID,
        options: TableContextOptions,
    ) -> TableAnalysisContext:
        """Build one table context and store it.

        When Neo4j is configured, the model writes a column subgraph for this table.
        PostgreSQL catalog rows stay unchanged.
        """
        logger.info(
            "build knowledge context start table_id=%s profile=%s graph=%s lineage=%s",
            table_id,
            options.include_data_profile,
            options.include_graph,
            options.include_lineage,
        )
        reader: LineageReader = self._lineage if self._lineage is not None else EmptyLineageReader()
        column_graph: ColumnSubgraphBuilder | None = None
        if self._neo4j is not None and self._column_proposer is not None:
            column_graph = ColumnSubgraphBuilder(
                candidates=ScopeColumnCatalog(RegistryRepository(self._session)),
                proposer=self._column_proposer,
                writer=Neo4jColumnSubgraph(self._neo4j),
            )
        factory: TableAgentFactory = build_table_agent_factory(
            self._session,
            self.graph,
            reader,
            UnconfiguredDataProfiler(),
            publisher=self.graph,
            operation_agent=self._operation_agent,
            column_graph=column_graph,
        )
        agent: TableAgent = factory.create(table_id)
        built: TableAnalysisContext = agent.build_context(options)
        KnowledgeContextStore(self._session).save(built)
        logger.info(
            "build knowledge context done table_id=%s columns=%s relations=%s",
            table_id,
            len(built.columns),
            len(built.column_relations),
        )
        return built

    def read_knowledge_context(self, table_id: UUID) -> TableAnalysisContext:
        """Return the last context saved for this table."""
        saved: TableAnalysisContext | None = KnowledgeContextStore(self._session).get(table_id)
        if saved is None:
            raise EntityNotFoundError("knowledge context", str(table_id))
        return saved

    def build_scope_context(
        self,
        scope_id: UUID,
        options: ScopeContextOptions,
    ) -> ScopeAnalysisContext:
        """Build one scope context and store it.

        The model reads saved table contexts and, when Neo4j is configured, each
        table's knowledge subgraph. Accepted links replace prior LLM relationships.
        """
        logger.info("build scope context start scope_id=%s", scope_id)
        readers: list[TableKnowledgeReader] = [
            PostgresTableKnowledgeReader(KnowledgeContextStore(self._session)),
        ]
        if self._neo4j is not None:
            readers.append(Neo4jTableKnowledgeReader(self._neo4j))
        writer: KnowledgeSubgraphWriter
        if self._neo4j is not None:
            writer = Neo4jScopeSubgraph(self._neo4j)
        else:
            writer = EmptyKnowledgeSubgraphWriter()
        builder: ScopeGraphBuilder = ScopeGraphBuilder(
            proposer=self._scope_proposer,
            writer=writer,
            relationships=LlmRelationshipStore(self._session),
            publisher=self.graph,
        )
        factory: ScopeAgentFactory = build_scope_agent_factory(
            PostgresScopeCatalogContextProvider(
                RegistryRepository(self._session),
                MergedTableKnowledgeReader(readers),
                ScopeKnowledgeContextStore(self._session),
            ),
            builder,
        )
        agent: ScopeAgent = factory.create(scope_id)
        built: ScopeAnalysisContext = agent.build_context(options)
        ScopeKnowledgeContextStore(self._session).save(built)
        logger.info(
            "build scope context done scope_id=%s links=%s missing=%s",
            scope_id,
            len(built.table_links),
            len(built.tables_without_context),
        )
        return built

    def read_scope_context(self, scope_id: UUID) -> ScopeAnalysisContext:
        """Return the last context saved for this scope."""
        saved: ScopeAnalysisContext | None = ScopeKnowledgeContextStore(self._session).get(scope_id)
        if saved is None:
            raise EntityNotFoundError("knowledge context", str(scope_id))
        return saved


class _UnconfiguredTrino:
    """Discovery stand-in used when Trino is not configured."""

    def discover(self, request: DiscoveryRequest) -> DiscoveryBatch:
        """Fail the scan instead of inventing metadata."""
        del request
        raise TrinoUnavailableError("Trino is not configured")
