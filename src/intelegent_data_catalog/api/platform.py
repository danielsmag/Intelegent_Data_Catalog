"""Request-scoped catalog platform. Discovery and query execution stay separate."""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.airflow.repository import AirflowRepository
from intelegent_data_catalog.assets.agent_pack.compose import (
    LoadedPack,
    active_task,
    allowed_tools,
    apply_explicit_pack_flags,
    compose_rules,
    describe_instructions,
    narrow_flags,
    scoped_pack,
)
from intelegent_data_catalog.assets.agent_pack.runtime import render_skills
from intelegent_data_catalog.assets.agent_pack.service import AgentPackService
from intelegent_data_catalog.assets.base import Asset, AssetAgentProvider
from intelegent_data_catalog.assets.models import AssetAnalysisContext, AssetContextOptions
from intelegent_data_catalog.assets.registry import AssetRegistry
from intelegent_data_catalog.assets.store import AssetKnowledgeStore
from intelegent_data_catalog.assets.strategies.instance import InstanceAssetAgentAdapter
from intelegent_data_catalog.assets.strategies.table import TableAssetAgentAdapter
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import AppSettings, ModelId, ReasoningEffort
from intelegent_data_catalog.entity_configuration import option_values
from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    GraphUnavailableError,
    TrinoUnavailableError,
)
from intelegent_data_catalog.graph.neo4j_store import (
    Neo4jColumnSubgraph,
    Neo4jProjection,
    Neo4jScopeSubgraph,
    Neo4jSpaceSubgraph,
)
from intelegent_data_catalog.graph.service import GraphService
from intelegent_data_catalog.knowledge.context.airflow_catalog import (
    PostgresAirflowCatalogContextProvider,
)
from intelegent_data_catalog.knowledge.context.airflow_graph import AirflowGraphBuilder
from intelegent_data_catalog.knowledge.context.column_graph import (
    ColumnSubgraphBuilder,
    KnowledgeSubgraphWriter,
    ScopeColumnCatalog,
)
from intelegent_data_catalog.knowledge.context.lineage import EmptyLineageReader
from intelegent_data_catalog.knowledge.context.pipelines import (
    AttachedPipelineReader,
    AttachedPipelineRunner,
    AttachedPipelineSource,
)
from intelegent_data_catalog.knowledge.context.scope_catalog import (
    MergedTableKnowledgeReader,
    Neo4jTableKnowledgeReader,
    PostgresScopeCatalogContextProvider,
    PostgresTableKnowledgeReader,
    TableKnowledgeReader,
)
from intelegent_data_catalog.knowledge.context.scope_graph import (
    EmptyKnowledgeSubgraphWriter,
    ScopeGraphAppender,
    ScopeGraphBuilder,
)
from intelegent_data_catalog.knowledge.context.space_catalog import (
    MergedScopeKnowledgeReader,
    Neo4jScopeKnowledgeReader,
    PostgresScopeKnowledgeReader,
    PostgresSpaceCatalogContextProvider,
    ScopeKnowledgeReader,
)
from intelegent_data_catalog.knowledge.context.space_graph import (
    SpaceGraphAppender,
    SpaceGraphBuilder,
)
from intelegent_data_catalog.knowledge.factories.airflow_agent_factory import (
    AirflowAgentFactory,
    build_airflow_agent_factory,
)
from intelegent_data_catalog.knowledge.factories.scope_agent_factory import (
    ScopeAgentFactory,
    build_scope_agent_factory,
)
from intelegent_data_catalog.knowledge.factories.space_agent_factory import (
    SpaceAgentFactory,
    build_space_agent_factory,
)
from intelegent_data_catalog.knowledge.factories.table_agent_factory import (
    TableAgentFactory,
    build_table_agent_factory,
)
from intelegent_data_catalog.knowledge.models import (
    AirflowAnalysisContext,
    AirflowContextOptions,
    ColumnRelation,
    ContextGenerationOptions,
    PipelineSummary,
    ScopeAnalysisContext,
    ScopeContextOptions,
    SpaceAnalysisContext,
    SpaceContextOptions,
    TableAnalysisContext,
    TableContextOptions,
    apply_instance_summaries,
    pipelines_missing_instance_summary,
)
from intelegent_data_catalog.knowledge.operations.airflow_operation import AirflowInstanceAgent
from intelegent_data_catalog.knowledge.operations.dag_operation import AirflowDagAgent
from intelegent_data_catalog.knowledge.operations.file_operation import FileAgent
from intelegent_data_catalog.knowledge.operations.scope_operation import ScopeAgent
from intelegent_data_catalog.knowledge.operations.space_operation import SpaceAgent
from intelegent_data_catalog.knowledge.operations.table_operation import TableAgent
from intelegent_data_catalog.knowledge.profiling.profiler import UnconfiguredDataProfiler
from intelegent_data_catalog.knowledge.store import (
    AirflowKnowledgeContextStore,
    KnowledgeContextStore,
    ScopeKnowledgeContextStore,
    SemanticContextStore,
    SpaceKnowledgeContextStore,
)
from intelegent_data_catalog.lineage.marquez import LineageReader, MarquezClient
from intelegent_data_catalog.llm.factory import CatalogModels, thinking_level
from intelegent_data_catalog.llm.overlay import DescribeOverlay, describe_overlay
from intelegent_data_catalog.persistence.enums import AssetKind, RefreshSubjectKind
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AirflowInstanceRow,
    AssetRow,
    ColumnRow,
    EntityConfigurationRow,
)
from intelegent_data_catalog.registry.annotations import CatalogAnnotations
from intelegent_data_catalog.registry.llm_relationships import LlmRelationshipStore
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.services.bindings import BindingService
from intelegent_data_catalog.services.scopes import ScopeService
from intelegent_data_catalog.services.spaces import SpaceService
from intelegent_data_catalog.services.tables import TableService
from intelegent_data_catalog.sources.discovery import DiscoveryBatch, DiscoveryRequest
from intelegent_data_catalog.sources.enrichment import PostgresForeignKeyEnricher
from intelegent_data_catalog.sources.sync import MetadataSyncService
from intelegent_data_catalog.sources.trino.client import TrinoClient
from intelegent_data_catalog.sources.trino.discovery import TrinoCatalogSource
from intelegent_data_catalog.storage.object_store import ObjectStore

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")
_JOB_USER: UUID = UUID(int=0)


def _job_access() -> AccessContext:
    """Visibility used when a worker loads a pack. ``load`` does not check it."""
    return AccessContext(
        principal=Principal(
            subject="catalog-job",
            email=None,
            name="catalog-job",
            roles=frozenset(),
            is_admin=True,
        ),
        user_id=_JOB_USER,
        workspace_id=None,
        member_workspace_ids=frozenset(),
        admin_workspace_ids=frozenset(),
        visible_space_ids=None,
    )


class RequestPlatform:
    """Services for one catalog request."""

    def __init__(
        self,
        session: Session,
        settings: AppSettings,
        query: QueryService,
        objects: ObjectStore | None = None,
        definitions: ObjectStore | None = None,
    ) -> None:
        self._session: Session = session
        self._definitions: ObjectStore | None = definitions
        self._settings: AppSettings = settings
        repo: RegistryRepository = RegistryRepository(session)
        self.spaces: SpaceService = SpaceService(repo)
        self.scopes: ScopeService = ScopeService(repo)
        self.tables: TableService = TableService(repo)
        self.bindings: BindingService = BindingService(repo)
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
        self._bundles: dict[tuple[ModelId, ReasoningEffort], CatalogModels] = {}
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
        self._objects: ObjectStore | None = objects

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
        models: CatalogModels = self._models(options.model, options.effort)
        column_graph: ColumnSubgraphBuilder | None = None
        if self._neo4j is not None and models.column is not None:
            column_graph = ColumnSubgraphBuilder(
                candidates=ScopeColumnCatalog(RegistryRepository(self._session)),
                proposer=models.column,
                writer=Neo4jColumnSubgraph(self._neo4j),
            )
        agent: TableAgent = self._table_factory(column_graph, models).create(table_id)
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
        models: CatalogModels = self._models(options.model, options.effort)
        writer: KnowledgeSubgraphWriter
        if self._neo4j is not None:
            writer = Neo4jScopeSubgraph(self._neo4j)
        else:
            writer = EmptyKnowledgeSubgraphWriter()
        builder: ScopeGraphBuilder = ScopeGraphBuilder(
            proposer=models.scope_proposer,
            writer=writer,
            relationships=LlmRelationshipStore(self._session),
            publisher=self.graph,
        )
        agent: ScopeAgent = self._scope_factory(builder, models).create(scope_id)
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
        filled: list[PipelineSummary] = self._fill_instance_summaries(saved.pipelines)
        if filled is saved.pipelines:
            return saved
        return saved.model_copy(update={"pipelines": filled})

    def build_space_context(
        self,
        space_id: UUID,
        options: SpaceContextOptions,
    ) -> SpaceAnalysisContext:
        """Build one space context and store it.

        The model reads each scope's saved context and, when Neo4j is configured,
        that scope's knowledge subgraph. Accepted links replace prior cross-scope
        LLM relationships.
        """
        logger.info("build space context start space_id=%s", space_id)
        models: CatalogModels = self._models(options.model, options.effort)
        writer: KnowledgeSubgraphWriter
        if self._neo4j is not None:
            writer = Neo4jSpaceSubgraph(self._neo4j)
        else:
            writer = EmptyKnowledgeSubgraphWriter()
        builder: SpaceGraphBuilder = SpaceGraphBuilder(
            proposer=models.space_proposer,
            writer=writer,
            relationships=LlmRelationshipStore(self._session),
            publisher=self.graph,
        )
        agent: SpaceAgent = self._space_factory(builder, models).create(space_id)
        built: SpaceAnalysisContext = agent.build_context(options)
        SpaceKnowledgeContextStore(self._session).save(built)
        logger.info(
            "build space context done space_id=%s links=%s missing=%s",
            space_id,
            len(built.table_links),
            len(built.scopes_without_context),
        )
        return built

    def read_space_context(self, space_id: UUID) -> SpaceAnalysisContext:
        """Return the last context saved for this space."""
        saved: SpaceAnalysisContext | None = SpaceKnowledgeContextStore(self._session).get(space_id)
        if saved is None:
            raise EntityNotFoundError("knowledge context", str(space_id))
        filled: list[PipelineSummary] = self._fill_instance_summaries(saved.pipelines)
        if filled is saved.pipelines:
            return saved
        return saved.model_copy(update={"pipelines": filled})

    def _fill_instance_summaries(self, pipelines: list[PipelineSummary]) -> list[PipelineSummary]:
        missing: set[str] = pipelines_missing_instance_summary(pipelines)
        if len(missing) == 0:
            return pipelines
        return apply_instance_summaries(pipelines, self._airflow_summaries(missing))

    def _airflow_summaries(self, names: set[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        store: AirflowKnowledgeContextStore = AirflowKnowledgeContextStore(self._session)
        row: AirflowInstanceRow
        for row in AirflowRepository(self._session).list_instances():
            if row.name not in names or row.name in found:
                continue
            saved: AirflowAnalysisContext | None = store.get(row.id)
            if saved is None or saved.summary is None:
                continue
            text: str = saved.summary.strip()
            if text == "":
                continue
            found[row.name] = text
        return found

    def generate_context(self, subject_id: UUID, options: ContextGenerationOptions) -> None:
        """Ask the space, table, or scope agent for business context. Origin is ``LLM``."""
        logger.info("generate context start kind=%s subject_id=%s", options.kind, subject_id)
        models: CatalogModels = self._models(options.model, options.effort)
        match options.kind:
            case "space":
                self._space_factory(None, models).create(subject_id).describe()
            case "scope":
                self._scope_factory(None, models).create(subject_id).describe()
            case "airflow":
                self._airflow_factory(None, models).create(subject_id).describe()
            case "table":
                self._table_factory(None, models).create(subject_id).describe_table()
            case "table_columns":
                self._table_factory(None, models).create(subject_id).describe_columns()
            case "column":
                row: ColumnRow | None = RegistryRepository(self._session).get_column(subject_id)
                if row is None:
                    raise EntityNotFoundError("column", str(subject_id))
                self._table_factory(None, models).create(row.table_id).describe_column(subject_id)
            case "asset":
                with describe_overlay(self._pack_overlay(subject_id)):
                    AssetRegistry(
                        self._session,
                        _PlatformAgents(
                            self,
                            AssetContextOptions(model=options.model, effort=options.effort),
                        ),
                    ).load(subject_id).describe()
        logger.info("generate context done kind=%s subject_id=%s", options.kind, subject_id)

    def build_asset_context(
        self,
        asset_id: UUID,
        options: AssetContextOptions,
    ) -> AssetAnalysisContext:
        """Build one asset context with the agent for its kind and store the snapshot."""
        logger.info("build asset context start asset_id=%s", asset_id)
        selected: AssetContextOptions = self._narrow_pack_flags(asset_id, options)
        with describe_overlay(self._pack_overlay(asset_id, selected.flags)):
            asset: Asset = AssetRegistry(
                self._session,
                _PlatformAgents(self, selected),
            ).load(asset_id)
            built: AssetAnalysisContext = asset.build_context(selected)
        logger.info("build asset context done asset_id=%s kind=%s", asset_id, built.kind.value)
        return built

    def _pack_service(self) -> AgentPackService | None:
        if self._definitions is None:
            return None
        return AgentPackService(
            self._session,
            self._definitions,
            self._settings.asset_definitions,
            _job_access(),
        )

    def _pack_overlay(
        self,
        asset_id: UUID,
        flags: dict[str, bool] | None = None,
    ) -> DescribeOverlay | None:
        service: AgentPackService | None = self._pack_service()
        if service is None or self._definitions is None:
            return None
        pack: LoadedPack = service.load(asset_id)
        chosen: dict[str, bool] = self._asset_flags(asset_id)
        if flags is not None:
            chosen.update(flags)
        chosen = apply_explicit_pack_flags(chosen)
        scoped: LoadedPack = scoped_pack(pack, chosen)
        rendered: str = render_skills(self._definitions, scoped)
        instructions: str | None = describe_instructions(
            scoped.agents,
            compose_rules(scoped.rules),
            rendered,
        )
        task: str | None = active_task(scoped.prompts)
        return DescribeOverlay(instructions, task, asset_id, self._definitions)

    def _asset_flags(self, asset_id: UUID) -> dict[str, bool]:
        """Saved build flags. Missing pack switches stay on, and the row is not written."""
        asset: AssetRow | None = self._session.get(AssetRow, asset_id)
        kind: str | None = None if asset is None else asset.kind
        row: EntityConfigurationRow | None = self._session.scalar(
            select(EntityConfigurationRow).where(EntityConfigurationRow.asset_id == asset_id),
        )
        raw: dict[str, object] = {} if row is None else dict(row.options)
        return option_values(RefreshSubjectKind.ASSET, raw, kind)

    def _narrow_pack_flags(
        self,
        asset_id: UUID,
        options: AssetContextOptions,
    ) -> AssetContextOptions:
        service: AgentPackService | None = self._pack_service()
        if service is None:
            return options
        row: AssetRow | None = self._session.get(AssetRow, asset_id)
        if row is None:
            return options
        pack: LoadedPack = service.load(asset_id)
        chosen: dict[str, bool] = self._asset_flags(asset_id)
        chosen.update(options.flags)
        chosen = apply_explicit_pack_flags(chosen)
        scoped: LoadedPack = scoped_pack(pack, chosen)
        allowed: set[str] | None = allowed_tools(scoped.skills, chosen, AssetKind(row.kind))
        return options.model_copy(update={"flags": narrow_flags(options.flags, allowed)})

    def read_asset_context(self, asset_id: UUID) -> AssetAnalysisContext:
        """Return the last context saved for this asset."""
        saved: AssetAnalysisContext | None = AssetKnowledgeStore(self._session).get(asset_id)
        if saved is None:
            raise EntityNotFoundError("knowledge context", str(asset_id))
        return saved

    def asset_name(self, asset_id: UUID) -> str:
        """Return the asset name, or raise when the id is unknown."""
        row: AssetRow | None = self._session.get(AssetRow, asset_id)
        if row is None:
            raise EntityNotFoundError("asset", str(asset_id))
        return row.name

    def _models(self, key: ModelId, effort: ReasoningEffort) -> CatalogModels:
        """Build the agents for one saved model and effort. Later calls reuse them."""
        cache_key: tuple[ModelId, ReasoningEffort] = (key, effort)
        cached: CatalogModels | None = self._bundles.get(cache_key)
        if cached is not None:
            return cached
        built: CatalogModels = CatalogModels(
            self._settings.llm.profile(key),
            text_chars=self._settings.context.text_chars,
            column_graph=self._neo4j is not None,
            thinking=thinking_level(effort),
        )
        self._bundles[cache_key] = built
        return built

    def _table_factory(
        self,
        column_graph: ColumnSubgraphBuilder | None,
        models: CatalogModels,
    ) -> TableAgentFactory:
        reader: LineageReader = self._lineage if self._lineage is not None else EmptyLineageReader()
        return build_table_agent_factory(
            self._session,
            self.graph,
            reader,
            UnconfiguredDataProfiler(),
            publisher=self.graph,
            operation_agent=models.operation,
            column_graph=column_graph,
            subgraph=None if self._neo4j is None else _TableSubgraph(self._neo4j),
        )

    def _scope_factory(
        self,
        graph_builder: ScopeGraphAppender | None,
        models: CatalogModels,
    ) -> ScopeAgentFactory:
        readers: list[TableKnowledgeReader] = [
            PostgresTableKnowledgeReader(KnowledgeContextStore(self._session)),
        ]
        if self._neo4j is not None:
            readers.append(Neo4jTableKnowledgeReader(self._neo4j))
        return build_scope_agent_factory(
            PostgresScopeCatalogContextProvider(
                RegistryRepository(self._session),
                MergedTableKnowledgeReader(readers),
                ScopeKnowledgeContextStore(self._session),
                pipelines=self._pipelines(graph_builder is not None, models),
                max_tables=self._settings.context.scope_max_tables,
                max_columns=self._settings.context.scope_max_columns,
            ),
            graph_builder,
            describer=models.scope_describer,
            context_writer=SemanticContextStore(self._session),
        )

    def _space_factory(
        self,
        graph_builder: SpaceGraphAppender | None,
        models: CatalogModels,
    ) -> SpaceAgentFactory:
        readers: list[ScopeKnowledgeReader] = [
            PostgresScopeKnowledgeReader(ScopeKnowledgeContextStore(self._session)),
        ]
        if self._neo4j is not None:
            readers.append(Neo4jScopeKnowledgeReader(self._neo4j))
        return build_space_agent_factory(
            PostgresSpaceCatalogContextProvider(
                RegistryRepository(self._session),
                MergedScopeKnowledgeReader(readers),
                SpaceKnowledgeContextStore(self._session),
                pipelines=self._pipelines(graph_builder is not None, models),
                max_scopes=self._settings.context.space_max_scopes,
                max_tables=self._settings.context.space_max_tables,
                max_columns=self._settings.context.space_max_columns,
            ),
            graph_builder,
            describer=models.space_describer,
            context_writer=SemanticContextStore(self._session),
        )

    def build_airflow_context(
        self,
        instance_id: UUID,
        options: AirflowContextOptions,
    ) -> AirflowAnalysisContext:
        """Build one Airflow instance context and store it.

        One model call covers the synced DAGs of this instance. Accepted links
        replace prior LLM DAG mappings for those DAGs.
        """
        logger.info("build airflow context start instance_id=%s", instance_id)
        models: CatalogModels = self._models(options.model, options.effort)
        repository: AirflowRepository = AirflowRepository(self._session)
        builder: AirflowGraphBuilder = AirflowGraphBuilder(models.airflow_proposer, repository)
        agent: AirflowInstanceAgent = self._airflow_factory(builder, models).create(instance_id)
        built: AirflowAnalysisContext = agent.build_context(options)
        AirflowKnowledgeContextStore(self._session).save(built)
        logger.info(
            "build airflow context done instance_id=%s dags=%s",
            instance_id,
            built.dags_analyzed,
        )
        return built

    def read_airflow_context(self, instance_id: UUID) -> AirflowAnalysisContext:
        """Return the last context saved for this Airflow instance."""
        saved: AirflowAnalysisContext | None = AirflowKnowledgeContextStore(self._session).get(
            instance_id,
        )
        if saved is None:
            raise EntityNotFoundError("knowledge context", str(instance_id))
        return saved

    def airflow_instance_name(self, instance_id: UUID) -> str:
        """Return the instance name, or raise when the id is unknown."""
        row: AirflowInstanceRow | None = AirflowRepository(self._session).get_instance(instance_id)
        if row is None:
            raise EntityNotFoundError("airflow instance", str(instance_id))
        return row.name

    def _airflow_factory(
        self,
        graph_builder: AirflowGraphBuilder | None,
        models: CatalogModels,
    ) -> AirflowAgentFactory:
        return build_airflow_agent_factory(
            PostgresAirflowCatalogContextProvider(
                AirflowRepository(self._session),
                RegistryRepository(self._session),
                AirflowKnowledgeContextStore(self._session),
                limits=self._settings.context,
            ),
            graph_builder,
            describer=models.airflow_describer,
            context_writer=SemanticContextStore(self._session),
        )

    def _pipelines(self, analyze: bool, models: CatalogModels) -> AttachedPipelineSource:
        repository: AirflowRepository = AirflowRepository(self._session)
        snapshots: AirflowKnowledgeContextStore = AirflowKnowledgeContextStore(self._session)
        if not analyze:
            reader: AttachedPipelineReader = AttachedPipelineReader(
                repository,
                snapshots,
                max_pipelines=self._settings.context.max_pipelines,
                max_tasks=self._settings.context.airflow_max_tasks,
            )
            return reader
        builder: AirflowGraphBuilder = AirflowGraphBuilder(models.airflow_proposer, repository)
        runner: AttachedPipelineRunner = AttachedPipelineRunner(
            repository,
            self._airflow_factory(builder, models),
            snapshots,
            max_pipelines=self._settings.context.max_pipelines,
            max_tasks=self._settings.context.airflow_max_tasks,
        )
        return runner


class _PlatformAgents(AssetAgentProvider):
    """Opens the agent for the model and effort stored on one build."""

    def __init__(self, platform: RequestPlatform, options: AssetContextOptions) -> None:
        self._platform: RequestPlatform = platform
        self._options: AssetContextOptions = options

    def for_table(self, table_id: UUID) -> TableAssetAgentAdapter:
        models: CatalogModels = self._platform._models(self._options.model, self._options.effort)
        column_graph: ColumnSubgraphBuilder | None = None
        if self._platform._neo4j is not None and models.column is not None:
            column_graph = ColumnSubgraphBuilder(
                candidates=ScopeColumnCatalog(RegistryRepository(self._platform._session)),
                proposer=models.column,
                writer=Neo4jColumnSubgraph(self._platform._neo4j),
            )
        agent: TableAgent = self._platform._table_factory(column_graph, models).create(table_id)
        return TableAssetAgentAdapter(table_id, agent, self._platform._session)

    def for_instance(self, instance_id: UUID) -> InstanceAssetAgentAdapter:
        models: CatalogModels = self._platform._models(self._options.model, self._options.effort)
        repository: AirflowRepository = AirflowRepository(self._platform._session)
        builder: AirflowGraphBuilder = AirflowGraphBuilder(models.airflow_proposer, repository)
        factory: AirflowAgentFactory = self._platform._airflow_factory(builder, models)
        return InstanceAssetAgentAdapter(
            instance_id,
            factory.create(instance_id),
            self._platform._session,
        )

    def for_dag(self, dag_row_id: UUID, instance_id: UUID) -> AirflowDagAgent:
        models: CatalogModels = self._platform._models(self._options.model, self._options.effort)
        repository: AirflowRepository = AirflowRepository(self._platform._session)
        builder: AirflowGraphBuilder = AirflowGraphBuilder(models.airflow_proposer, repository)
        factory: AirflowAgentFactory = self._platform._airflow_factory(builder, models)
        instance_agent: AirflowInstanceAgent = factory.create(instance_id)
        dag: AirflowDagRow | None = self._platform._session.get(AirflowDagRow, dag_row_id)
        if dag is None:
            raise EntityNotFoundError("airflow dag", str(dag_row_id))
        return AirflowDagAgent(dag, instance_agent, models.dag_describer)

    def for_file(self, asset_id: UUID, notebook: bool) -> FileAgent:
        del notebook
        models: CatalogModels = self._platform._models(self._options.model, self._options.effort)
        asset: AssetRow | None = self._platform._session.get(AssetRow, asset_id)
        if asset is None:
            raise EntityNotFoundError("asset", str(asset_id))
        return FileAgent(
            asset,
            self._platform._session,
            self._platform._objects,
            models.file_describer,
        )


class _TableSubgraph:
    """Column links from the Neo4j knowledge subgraph owned by one table."""

    def __init__(self, graphs: Neo4jProjection) -> None:
        self._reader: Neo4jTableKnowledgeReader = Neo4jTableKnowledgeReader(graphs)

    def read(self, table_id: UUID) -> list[ColumnRelation]:
        """Return stored links. A Neo4j outage leaves the saved snapshot as the only source."""
        try:
            return self._reader.read(table_id).relations
        except GraphUnavailableError:
            return []


class _UnconfiguredTrino:
    """Discovery stand-in used when Trino is not configured."""

    def discover(self, request: DiscoveryRequest) -> DiscoveryBatch:
        """Fail the scan instead of inventing metadata."""
        del request
        raise TrinoUnavailableError("Trino is not configured")
