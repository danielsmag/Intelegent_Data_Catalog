"""Table agent foundation: metadata, profiles, graph neighbors, and lineage."""

import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryFeedbackPart,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    GraphUnavailableError,
    LlmError,
    LlmUnavailableError,
    ValidationAppError,
)
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView
from intelegent_data_catalog.graph.projection import LineageLink
from intelegent_data_catalog.graph.service import GraphService
from intelegent_data_catalog.knowledge.context.column_graph import ColumnGraphAppender
from intelegent_data_catalog.knowledge.context.graph import ProjectionGraphContextProvider
from intelegent_data_catalog.knowledge.context.lineage import MarquezLineageContextProvider
from intelegent_data_catalog.knowledge.factories.table_agent_factory import (
    TableAgentFactory,
    build_table_agent_factory,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnComparisonProfile,
    ColumnDataProfile,
    ColumnMappingSummary,
    ColumnMetadata,
    DataAccessMode,
    DataProfileOptions,
    DatasetIdentity,
    GraphNeighbor,
    KnowledgeDefaults,
    LineageContext,
    NeighborDirection,
    ProfileOverrides,
    ScopeSummary,
    SpaceSummary,
    TableAnalysisContext,
    TableCatalogContext,
    TableContextOptions,
    TableDataProfile,
    TableMetadata,
    TableRelation,
    TagSummary,
)
from intelegent_data_catalog.knowledge.operations.table_operation import TableAgent
from intelegent_data_catalog.knowledge.profiling.profiler import (
    DataProfiler,
    UnconfiguredDataProfiler,
)
from intelegent_data_catalog.llm.catalog import TableOperationAgent
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    DataSourceType,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    TableKind,
    TableStatus,
)
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.registry.annotations import (
    CatalogAnnotations,
    MarquezMappingWrite,
    TagWrite,
)
from intelegent_data_catalog.registry.schemas import (
    CatalogTableCreate,
    ColumnCreate,
    ColumnMappingWrite,
    DataSourceCreate,
    RelationshipCreate,
    ScopeCreate,
    SourceRef,
    SpaceCreate,
)
from tests.registry_factory import OpenRegistry, open_registry


def test_default_access_mode_is_profile_only() -> None:
    """Factory limits start at statistics, not raw rows."""
    defaults: KnowledgeDefaults = KnowledgeDefaults()
    assert defaults.data_access_mode == DataAccessMode.PROFILE_ONLY
    assert defaults.max_sample_values == 5


def test_run_without_model_raises() -> None:
    """run fails until the factory is given a language model."""
    table_id: UUID = uuid4()
    agent: TableAgent = _factory(_context(table_id)).create(table_id)
    with pytest.raises(LlmUnavailableError, match="not configured"):
        agent.run("publish the graph")


def test_run_rejects_blank_instruction() -> None:
    """An empty instruction never reaches the model."""
    table_id: UUID = uuid4()
    agent: TableAgent = _factory(_context(table_id)).create(table_id)
    with pytest.raises(ValidationAppError, match="instruction"):
        agent.run("   ")


def test_run_reads_metadata_through_tools(caplog: pytest.LogCaptureFixture) -> None:
    """The model can load this table, and the tool result includes the catalog name."""
    caplog.set_level(logging.INFO, logger="intelegent_data_catalog.llm")
    table_id: UUID = uuid4()
    seen: list[str] = []
    names: set[str] = set()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        names.update(tool.name for tool in info.function_tools)
        feedback: str = _feedback(messages)
        if feedback == "":
            return ModelResponse(parts=[ToolCallPart(tool_name="get_metadata", args={})])
        seen.append(feedback)
        return ModelResponse(parts=[TextPart(content="The table is orders.")])

    agent: TableAgent = _factory(
        _context(table_id),
        operation_agent=_operation_agent(respond),
    ).create(table_id)
    answer: str = agent.run("describe this table")
    assert answer == "The table is orders."
    assert "describe this table" in caplog.text
    assert "get_metadata" in caplog.text
    assert seen
    assert "orders" in seen[0]
    assert names == {
        "get_metadata",
        "get_data_profile",
        "get_column_profile",
        "compare_columns",
        "get_neighbors",
        "get_lineage",
        "publish_graph",
    }


def test_run_publishes_the_neo4j_projection() -> None:
    """publish_graph replaces the projection and reports the written counts."""
    table_id: UUID = uuid4()
    publisher: _Publisher = _Publisher()
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        feedback: str = _feedback(messages)
        if feedback == "":
            return ModelResponse(parts=[ToolCallPart(tool_name="publish_graph", args={})])
        seen.append(feedback)
        return ModelResponse(parts=[TextPart(content="Wrote the projection.")])

    agent: TableAgent = _factory(
        _context(table_id),
        publisher=publisher,
        operation_agent=_operation_agent(respond),
    ).create(table_id)
    answer: str = agent.run("publish the graph")
    assert answer == "Wrote the projection."
    assert publisher.calls == 1
    assert seen
    assert "nodes" in seen[0]
    assert "edges" in seen[0]
    assert "3" in seen[0]
    assert "2" in seen[0]


def test_run_reports_missing_neo4j() -> None:
    """A publish with no Neo4j publisher is reported back to the model."""
    table_id: UUID = uuid4()
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        feedback: str = _feedback(messages)
        if feedback == "":
            return ModelResponse(parts=[ToolCallPart(tool_name="publish_graph", args={})])
        seen.append(feedback)
        return ModelResponse(parts=[TextPart(content="Neo4j is not configured.")])

    agent: TableAgent = _factory(
        _context(table_id),
        operation_agent=_operation_agent(respond),
    ).create(table_id)
    answer: str = agent.run("publish the graph")
    assert answer == "Neo4j is not configured."
    assert seen
    assert "not configured" in seen[0]


def test_publish_graph_without_neo4j_raises() -> None:
    """A direct publish fails when no publisher was injected."""
    table_id: UUID = uuid4()
    agent: TableAgent = _factory(_context(table_id)).create(table_id)
    with pytest.raises(GraphUnavailableError, match="not configured"):
        agent.publish_graph()


def test_run_returns_invalid_column_to_the_model() -> None:
    """A column id that is not a uuid is returned to the model."""
    table_id: UUID = uuid4()
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        feedback: str = _feedback(messages)
        if feedback == "":
            return ModelResponse(
                parts=[ToolCallPart(tool_name="get_column_profile", args={"column_id": "nope"})]
            )
        seen.append(feedback)
        return ModelResponse(parts=[TextPart(content="The column id was invalid.")])

    agent: TableAgent = _factory(
        _context(table_id),
        operation_agent=_operation_agent(respond),
    ).create(table_id)
    answer: str = agent.run("profile the column")
    assert answer == "The column id was invalid."
    assert seen
    assert "uuid" in seen[0].lower()


def test_run_stops_at_the_request_limit() -> None:
    """A model that keeps calling tools is cut off."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[ToolCallPart(tool_name="get_metadata", args={})])

    table_id: UUID = uuid4()
    agent: TableAgent = _factory(
        _context(table_id),
        operation_agent=_operation_agent(respond, max_requests=1),
    ).create(table_id)
    with pytest.raises(LlmError, match="tool step limit"):
        agent.run("loop")


def test_factory_creates_a_fresh_agent() -> None:
    """Two creates for one table id are different objects."""
    table_id: UUID = uuid4()
    factory: TableAgentFactory = _factory(_context(table_id))
    first: TableAgent = factory.create(table_id)
    second: TableAgent = factory.create(table_id)
    assert first is not second
    assert first.table_id == table_id
    assert second.table_id == table_id


def test_agent_reloads_metadata() -> None:
    """A second read sees catalog changes. The agent does not keep a copy."""
    table_id: UUID = uuid4()
    catalog: _Catalog = _Catalog(_context(table_id))
    agent: TableAgent = _factory(catalog.context, catalog=catalog).create(table_id)
    assert agent.get_metadata().table.name == "orders"
    catalog.context = _context(table_id, name="orders_v2")
    assert agent.get_metadata().table.name == "orders_v2"
    assert catalog.calls == 2


def test_missing_table_raises() -> None:
    """An unknown catalog id fails before profiling."""
    agent: TableAgent = _factory(_context(uuid4()), catalog=_MissingCatalog()).create(uuid4())
    with pytest.raises(EntityNotFoundError, match="table"):
        agent.get_metadata()


def test_metadata_exposes_keys_scope_space_and_tags() -> None:
    """The catalog context carries columns, keys, relationships, and hierarchy."""
    table_id: UUID = uuid4()
    column_id: UUID = uuid4()
    context: TableCatalogContext = _context(table_id, column_id=column_id, foreign_key=True)
    agent: TableAgent = _factory(context).create(table_id)
    loaded: TableCatalogContext = agent.get_metadata()
    assert loaded.table.space.name == "Commerce"
    assert loaded.table.scope.name == "Ordering"
    assert loaded.table.tags[0].name == "finance"
    assert loaded.columns[0].id == column_id
    assert loaded.columns[0].is_primary_key is True
    assert loaded.columns[0].is_foreign_key is True
    assert loaded.relationships[0].relationship_type == RelationshipType.FOREIGN_KEY
    assert loaded.relationships[0].column_mappings[0].source_column_id == column_id


def test_profile_only_drops_sample_values() -> None:
    """Default profiling keeps statistics and discards raw values."""
    table_id: UUID = uuid4()
    column_id: UUID = uuid4()
    profiler: _Profiler = _Profiler(_table_profile(table_id, column_id, samples=["secret"]))
    agent: TableAgent = _factory(_context(table_id, column_id=column_id), profiler=profiler).create(
        table_id
    )
    profile: TableDataProfile = agent.get_data_profile()
    assert profile.access_mode == DataAccessMode.PROFILE_ONLY
    assert profile.columns[0].distinct_count == 4
    assert profile.columns[0].sample_values is None
    assert profiler.table_options[0] is not None
    assert profiler.table_options[0].max_sample_values == 0


def test_sampled_mode_caps_values_at_the_factory_limit() -> None:
    """A sampled request cannot exceed the configured sample cap or timeout."""
    table_id: UUID = uuid4()
    column_id: UUID = uuid4()
    profiler: _Profiler = _Profiler(
        _table_profile(table_id, column_id, samples=["a", "b", "c", "d"])
    )
    defaults: KnowledgeDefaults = KnowledgeDefaults(
        max_sample_values=2,
        profile_timeout_seconds=10,
    )
    agent: TableAgent = _factory(
        _context(table_id, column_id=column_id),
        profiler=profiler,
        defaults=defaults,
    ).create(table_id)
    profile: TableDataProfile = agent.get_data_profile(
        ProfileOverrides(
            access_mode=DataAccessMode.SAMPLED,
            timeout_seconds=50,
        )
    )
    assert profile.columns[0].sample_values == ["a", "b"]
    sent: DataProfileOptions | None = profiler.table_options[0]
    assert sent is not None
    assert sent.max_sample_values == 2
    assert sent.timeout_seconds == 10


def test_metadata_only_does_not_call_the_profiler() -> None:
    """Metadata-only access returns an empty profile and skips physical reads."""
    table_id: UUID = uuid4()
    profiler: _Profiler = _Profiler(_table_profile(table_id, uuid4()))
    agent: TableAgent = _factory(_context(table_id), profiler=profiler).create(table_id)
    profile: TableDataProfile = agent.get_data_profile(
        ProfileOverrides(access_mode=DataAccessMode.METADATA_ONLY)
    )
    assert profile.access_mode == DataAccessMode.METADATA_ONLY
    assert profile.columns == []
    assert profiler.table_options == []


def test_column_profile_rejects_a_column_from_another_table() -> None:
    """The agent does not profile a column it does not own."""
    table_id: UUID = uuid4()
    profiler: _Profiler = _Profiler(_table_profile(table_id, uuid4()))
    agent: TableAgent = _factory(_context(table_id), profiler=profiler).create(table_id)
    with pytest.raises(ValidationAppError, match="does not belong"):
        agent.get_column_profile(uuid4())
    assert profiler.column_calls == 0


def test_profiler_cannot_substitute_another_column() -> None:
    """A column id that differs from the request is rejected."""
    table_id: UUID = uuid4()
    column_id: UUID = uuid4()
    profiler: _Profiler = _Profiler(_table_profile(table_id, column_id))
    profiler.column_profile = _column_profile(uuid4(), samples=None)
    agent: TableAgent = _factory(_context(table_id, column_id=column_id), profiler=profiler).create(
        table_id
    )
    with pytest.raises(ValidationAppError, match="different column"):
        agent.get_column_profile(column_id)


def test_comparison_rejects_a_swapped_pair() -> None:
    """The agent does not repair column ids returned by the profiler."""
    table_id: UUID = uuid4()
    source_id: UUID = uuid4()
    target_id: UUID = uuid4()
    profiler: _Profiler = _Profiler(_table_profile(table_id, source_id))
    profiler.comparison = ColumnComparisonProfile(
        source_column_id=target_id,
        target_column_id=source_id,
        datatype_compatible=True,
        value_overlap_ratio=0.5,
    )
    agent: TableAgent = _factory(_context(table_id, column_id=source_id), profiler=profiler).create(
        table_id
    )
    with pytest.raises(ValidationAppError, match="different column pair"):
        agent.compare_columns(source_id, target_id)


def test_comparison_returns_ratios_for_a_column_on_this_table() -> None:
    """Overlap stays numeric. The source column must belong to the agent table."""
    table_id: UUID = uuid4()
    source_id: UUID = uuid4()
    target_id: UUID = uuid4()
    profiler: _Profiler = _Profiler(_table_profile(table_id, source_id))
    profiler.comparison = ColumnComparisonProfile(
        source_column_id=source_id,
        target_column_id=target_id,
        datatype_compatible=True,
        value_overlap_ratio=0.984,
        source_coverage_ratio=0.982,
        target_uniqueness_ratio=1.0,
    )
    agent: TableAgent = _factory(_context(table_id, column_id=source_id), profiler=profiler).create(
        table_id
    )
    compared: ColumnComparisonProfile = agent.compare_columns(source_id, target_id)
    assert compared.value_overlap_ratio == 0.984
    assert compared.target_uniqueness_ratio == 1.0
    with pytest.raises(ValidationAppError, match="does not belong"):
        agent.compare_columns(target_id, source_id)


def test_neighbors_keep_catalog_ids_and_ignore_unresolved_nodes() -> None:
    """Graph neighbors are catalog tables on RELATED_TO edges."""
    orders: UUID = uuid4()
    customers: UUID = uuid4()
    regions: UUID = uuid4()
    view: GraphView = GraphView(
        nodes=[
            GraphNode(id=f"table:{orders}", kind="table", label="orders", catalog_id=str(orders)),
            GraphNode(
                id=f"table:{customers}",
                kind="table",
                label="customers",
                catalog_id=str(customers),
            ),
            GraphNode(
                id=f"table:{regions}",
                kind="table",
                label="regions",
                catalog_id=str(regions),
            ),
            GraphNode(id="dataset:raw:events", kind="table", label="events", catalog_id=None),
        ],
        edges=[
            GraphEdge(
                source=f"table:{orders}",
                target=f"table:{customers}",
                kind="RELATED_TO",
                style="relationship",
            ),
            GraphEdge(
                source=f"table:{regions}",
                target=f"table:{orders}",
                kind="RELATED_TO",
                style="relationship",
            ),
            GraphEdge(
                source=f"table:{orders}",
                target="dataset:raw:events",
                kind="RELATED_TO",
                style="relationship",
            ),
            GraphEdge(
                source="column:a",
                target="column:b",
                kind="REFERENCES",
                style="relationship",
            ),
        ],
        truncated=False,
    )
    neighbors: list[GraphNeighbor] = ProjectionGraphContextProvider(_GraphView(view)).get_neighbors(
        orders
    )
    assert [(item.table_id, item.direction, item.name) for item in neighbors] == [
        (customers, NeighborDirection.OUTGOING, "customers"),
        (regions, NeighborDirection.INCOMING, "regions"),
    ]


def test_lineage_records_dependency_without_inventing_a_join() -> None:
    """A job between two datasets is upstream or downstream context, not a relationship."""
    orders: UUID = uuid4()
    customers: UUID = uuid4()
    datasets: _Datasets = _Datasets(
        DatasetIdentity(catalog_table_id=orders, namespace="commerce", name="orders"),
        {("commerce", "customers"): customers},
    )
    links: _Links = _Links(
        [
            LineageLink(
                input_namespace="commerce",
                input_name="customers",
                job_namespace="commerce",
                job_name="build_orders",
                output_namespace="commerce",
                output_name="orders",
            ),
            LineageLink(
                input_namespace="commerce",
                input_name="orders",
                job_namespace="commerce",
                job_name="ship",
                output_namespace="commerce",
                output_name="payments",
            ),
            LineageLink(
                input_namespace="commerce",
                input_name="orders",
                job_namespace="commerce",
                job_name="build_orders",
                output_namespace="commerce",
                output_name="orders",
            ),
        ]
    )
    lineage: LineageContext = MarquezLineageContextProvider(links, datasets).get_lineage(orders)
    assert lineage.upstream_datasets[0].catalog_table_id == customers
    assert lineage.upstream_datasets[0].resolved is True
    assert [item.catalog_table_id for item in lineage.upstream_datasets] == [customers]
    assert lineage.downstream_datasets[0].name == "payments"
    assert lineage.downstream_datasets[0].resolved is False
    assert [item.name for item in lineage.producer_jobs] == ["build_orders"]
    assert [item.name for item in lineage.consumer_jobs] == ["ship", "build_orders"]


def test_unmapped_table_has_empty_lineage() -> None:
    """No Marquez mapping means no lineage, not a guessed dataset name."""
    table_id: UUID = uuid4()
    lineage: LineageContext = MarquezLineageContextProvider(
        _Links([]),
        _Datasets(None, {}),
    ).get_lineage(table_id)
    assert lineage.table_id == table_id
    assert lineage.upstream_datasets == []
    assert lineage.downstream_datasets == []
    assert lineage.producer_jobs == []
    assert lineage.consumer_jobs == []


def test_build_context_combines_metadata_profile_graph_and_lineage() -> None:
    """One context call reads each provider and still strips samples."""
    table_id: UUID = uuid4()
    column_id: UUID = uuid4()
    neighbor: GraphNeighbor = GraphNeighbor(
        table_id=uuid4(),
        name="customers",
        edge_kind="RELATED_TO",
        direction=NeighborDirection.OUTGOING,
    )
    profiler: _Profiler = _Profiler(_table_profile(table_id, column_id, samples=["secret"]))
    graph: _Neighbors = _Neighbors([neighbor])
    lineage: _Lineage = _Lineage(table_id)
    agent: TableAgent = _factory(
        _context(table_id, column_id=column_id),
        profiler=profiler,
        graph=graph,
        lineage=lineage,
    ).create(table_id)
    context: TableAnalysisContext = agent.build_context()
    assert context.table.id == table_id
    assert context.columns[0].id == column_id
    assert context.data_profile is not None
    assert context.data_profile.columns[0].sample_values is None
    assert context.existing_relationships[0].source_table_id == table_id
    assert context.graph_neighbors == [neighbor]
    assert context.lineage is not None
    assert context.lineage.table_id == table_id
    metadata_only: TableAnalysisContext = agent.build_context(
        TableContextOptions(
            include_data_profile=True,
            profile=ProfileOverrides(access_mode=DataAccessMode.METADATA_ONLY),
        )
    )
    assert metadata_only.data_profile is None
    assert len(profiler.table_options) == 1


def test_build_context_skips_an_unconfigured_profiler() -> None:
    """A missing profiler does not fail the rest of the table context."""
    table_id: UUID = uuid4()
    agent: TableAgent = _factory(
        _context(table_id),
        profiler=UnconfiguredDataProfiler(),
    ).create(table_id)
    context: TableAnalysisContext = agent.build_context(
        TableContextOptions(include_data_profile=True)
    )
    assert context.data_profile is None
    assert context.table.id == table_id


def test_build_context_logs_requested_slices(caplog: pytest.LogCaptureFixture) -> None:
    """Build logs the table id and which slices were requested."""
    caplog.set_level(logging.INFO, logger="intelegent_data_catalog.knowledge")
    table_id: UUID = uuid4()
    agent: TableAgent = _factory(_context(table_id)).create(table_id)
    agent.build_context(
        TableContextOptions(
            include_data_profile=False,
            include_graph=True,
            include_lineage=False,
        )
    )
    assert str(table_id) in caplog.text
    assert "profile=False" in caplog.text
    assert "graph=True" in caplog.text
    assert "lineage=False" in caplog.text


def test_build_context_can_skip_graph_and_lineage() -> None:
    """Flags avoid reads the caller did not ask for."""
    table_id: UUID = uuid4()
    graph: _Neighbors = _Neighbors([])
    lineage: _Lineage = _Lineage(table_id)
    agent: TableAgent = _factory(
        _context(table_id),
        graph=graph,
        lineage=lineage,
    ).create(table_id)
    context: TableAnalysisContext = agent.build_context(
        TableContextOptions(include_graph=False, include_lineage=False, include_data_profile=False)
    )
    assert context.graph_neighbors == []
    assert context.lineage is None
    assert context.data_profile is None
    assert graph.calls == 0
    assert lineage.calls == 0


def test_session_factory_loads_postgres_graph_and_lineage(tmp_path: Path) -> None:
    """A real catalog session feeds metadata, projection neighbors, and mapped lineage."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        service: OpenRegistry = open_registry(session)
        annotations: CatalogAnnotations = CatalogAnnotations(session)
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
        ordering_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id
        customer_scope_id: UUID = service.scopes.create_scope(
            space_id,
            ScopeCreate(name="Customer"),
        ).id
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        orders_id: UUID = _table(service, ordering_id, source_id, "orders")
        customers_id: UUID = _table(service, customer_scope_id, source_id, "customers")
        order_key: UUID = service.tables.create_column(
            orders_id,
            ColumnCreate(name="id", data_type="bigint", ordinal_position=0, is_primary_key=True),
        ).id
        order_customer: UUID = service.tables.create_column(
            orders_id,
            ColumnCreate(name="customer_id", data_type="bigint", ordinal_position=1),
        ).id
        customer_key: UUID = service.tables.create_column(
            customers_id,
            ColumnCreate(
                name="id",
                data_type="bigint",
                ordinal_position=0,
                is_primary_key=True,
                is_unique=True,
            ),
        ).id
        service.relationships.create_relationship(
            RelationshipCreate(
                source_table_id=orders_id,
                target_table_id=customers_id,
                relationship_type=RelationshipType.FOREIGN_KEY,
                origin=RelationshipOrigin.DATABASE_CONSTRAINT,
                status=RelationshipStatus.VERIFIED,
                description="order customer",
                column_mappings=[
                    ColumnMappingWrite(
                        source_column_id=order_customer,
                        target_column_id=customer_key,
                    )
                ],
            )
        )
        tag_id: UUID = annotations.create_tag(TagWrite(name="finance")).id
        annotations.attach_tag(orders_id, tag_id)
        annotations.create_mapping(
            MarquezMappingWrite(
                catalog_table_id=orders_id,
                marquez_namespace="commerce",
                marquez_dataset_name="orders",
            )
        )
        annotations.create_mapping(
            MarquezMappingWrite(
                catalog_table_id=customers_id,
                marquez_namespace="commerce",
                marquez_dataset_name="customers",
            )
        )
        profiler: _Profiler = _Profiler(
            _table_profile(orders_id, order_customer, samples=["secret"])
        )
        factory: TableAgentFactory = build_table_agent_factory(
            session,
            GraphService(session),
            _Links(
                [
                    LineageLink(
                        input_namespace="commerce",
                        input_name="customers",
                        job_namespace="commerce",
                        job_name="build_orders",
                        output_namespace="commerce",
                        output_name="orders",
                    )
                ]
            ),
            profiler,
        )
        agent: TableAgent = factory.create(orders_id)
        loaded: TableCatalogContext = agent.get_metadata()
        assert loaded.table.space.slug == "commerce"
        assert loaded.table.scope.slug == "ordering"
        assert loaded.table.tags[0].name == "finance"
        by_name: dict[str, ColumnMetadata] = {column.name: column for column in loaded.columns}
        assert by_name["id"].id == order_key
        assert by_name["id"].is_primary_key is True
        assert by_name["id"].is_foreign_key is False
        assert by_name["customer_id"].is_foreign_key is True
        assert loaded.relationships[0].target_table_id == customers_id
        neighbors: list[GraphNeighbor] = agent.get_neighbors()
        assert [(item.table_id, item.direction) for item in neighbors] == [
            (customers_id, NeighborDirection.OUTGOING)
        ]
        lineage: LineageContext = agent.get_lineage()
        assert lineage.upstream_datasets[0].catalog_table_id == customers_id
        assert lineage.producer_jobs[0].name == "build_orders"
        assert lineage.downstream_datasets == []
        context: TableAnalysisContext = agent.build_context()
        assert context.data_profile is not None
        assert context.data_profile.columns[0].sample_values is None
        assert context.graph_neighbors[0].table_id == customers_id
        assert context.lineage is not None
        assert factory.create(orders_id) is not agent
    finally:
        session.close()
        database.dispose()


class _Catalog:
    """In-memory catalog provider."""

    def __init__(self, context: TableCatalogContext) -> None:
        self.context: TableCatalogContext = context
        self.calls: int = 0

    def get_table_context(self, table_id: UUID) -> TableCatalogContext:
        """Return the scripted context when the id matches."""
        self.calls += 1
        if table_id != self.context.table.id:
            raise EntityNotFoundError("table", str(table_id))
        return self.context


class _MissingCatalog:
    """Catalog provider that has no tables."""

    def get_table_context(self, table_id: UUID) -> TableCatalogContext:
        """Fail every lookup."""
        raise EntityNotFoundError("table", str(table_id))


class _Profiler:
    """Profiler double. Records the options the agent resolved."""

    def __init__(self, table_profile: TableDataProfile) -> None:
        self.table_profile: TableDataProfile = table_profile
        self.column_profile: ColumnDataProfile = _column_profile(uuid4(), samples=None)
        self.comparison: ColumnComparisonProfile = ColumnComparisonProfile(
            source_column_id=uuid4(),
            target_column_id=uuid4(),
            datatype_compatible=True,
        )
        self.table_options: list[DataProfileOptions | None] = []
        self.column_calls: int = 0
        self.compare_calls: int = 0

    def profile_table(
        self,
        table_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> TableDataProfile:
        """Return the scripted table profile."""
        del table_id
        self.table_options.append(options)
        return self.table_profile

    def profile_column(
        self,
        column_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> ColumnDataProfile:
        """Return the scripted column profile."""
        del column_id, options
        self.column_calls += 1
        return self.column_profile

    def compare_columns(
        self,
        source_column_id: UUID,
        target_column_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> ColumnComparisonProfile:
        """Return the scripted comparison."""
        del source_column_id, target_column_id, options
        self.compare_calls += 1
        return self.comparison


class _Neighbors:
    """Graph provider double."""

    def __init__(self, neighbors: list[GraphNeighbor]) -> None:
        self.neighbors: list[GraphNeighbor] = neighbors
        self.calls: int = 0

    def get_neighbors(self, table_id: UUID) -> list[GraphNeighbor]:
        """Return the scripted neighbors."""
        del table_id
        self.calls += 1
        return self.neighbors


class _Lineage:
    """Lineage provider double."""

    def __init__(self, table_id: UUID) -> None:
        self.context: LineageContext = LineageContext(
            table_id=table_id,
            upstream_datasets=[],
            downstream_datasets=[],
            producer_jobs=[],
            consumer_jobs=[],
        )
        self.calls: int = 0

    def get_lineage(self, table_id: UUID) -> LineageContext:
        """Return the scripted lineage context."""
        del table_id
        self.calls += 1
        return self.context


class _GraphView:
    """Relationship reader that returns one view."""

    def __init__(self, view: GraphView) -> None:
        self.view: GraphView = view

    def relationships(self, table_id: UUID, depth: int) -> GraphView:
        """Return the scripted view."""
        del table_id, depth
        return self.view


class _Datasets:
    """Dataset catalog double."""

    def __init__(
        self,
        identity: DatasetIdentity | None,
        tables: dict[tuple[str, str], UUID],
    ) -> None:
        self.identity: DatasetIdentity | None = identity
        self.tables: dict[tuple[str, str], UUID] = tables

    def dataset_for_table(self, table_id: UUID) -> DatasetIdentity | None:
        """Return the scripted identity when the table matches."""
        if self.identity is None or self.identity.catalog_table_id != table_id:
            return None
        return self.identity

    def table_for_dataset(self, namespace: str, name: str) -> UUID | None:
        """Return a mapped catalog id."""
        return self.tables.get((namespace, name))


class _Links:
    """Lineage reader double."""

    def __init__(self, links: list[LineageLink]) -> None:
        self.links: list[LineageLink] = links

    def read_links(self) -> list[LineageLink]:
        """Return the scripted links."""
        return self.links


class _Publisher:
    """Records one projection rebuild."""

    def __init__(self) -> None:
        self.calls: int = 0

    def rebuild(self) -> dict[str, int]:
        """Return scripted node and edge counts."""
        self.calls += 1
        return {"nodes": 3, "edges": 2}


def _factory(
    context: TableCatalogContext,
    *,
    catalog: _Catalog | _MissingCatalog | None = None,
    profiler: DataProfiler | None = None,
    graph: _Neighbors | None = None,
    lineage: _Lineage | None = None,
    defaults: KnowledgeDefaults | None = None,
    publisher: _Publisher | None = None,
    operation_agent: TableOperationAgent | None = None,
    column_graph: ColumnGraphAppender | None = None,
) -> TableAgentFactory:
    selected_profiler: DataProfiler = profiler or _Profiler(
        _table_profile(context.table.id, context.columns[0].id)
    )
    return TableAgentFactory(
        catalog=catalog or _Catalog(context),
        profiler=selected_profiler,
        graph=graph or _Neighbors([]),
        lineage=lineage or _Lineage(context.table.id),
        defaults=defaults,
        publisher=publisher,
        operation_agent=operation_agent,
        column_graph=column_graph,
    )


def _operation_agent(
    respond: Callable[[list[ModelMessage], AgentInfo], ModelResponse],
    *,
    max_requests: int = 4,
) -> TableOperationAgent:
    return TableOperationAgent(
        FunctionModel(respond),
        max_requests=max_requests,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )


def _feedback(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    seen_user: bool = False
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart | RetryPromptPart | RetryFeedbackPart):
                chunks.append(str(part.content))
            elif isinstance(part, UserPromptPart):
                if seen_user:
                    chunks.append(str(part.content))
                seen_user = True
    return "\n".join(chunks)


def _context(
    table_id: UUID,
    *,
    name: str = "orders",
    column_id: UUID | None = None,
    foreign_key: bool = False,
) -> TableCatalogContext:
    space_id: UUID = uuid4()
    scope_id: UUID = uuid4()
    selected_column: UUID = column_id or uuid4()
    target_column: UUID = uuid4()
    target_table: UUID = uuid4()
    return TableCatalogContext(
        table=TableMetadata(
            id=table_id,
            name=name,
            display_name=None,
            description="customer purchases",
            source_description=None,
            table_type=TableKind.TABLE,
            status=TableStatus.ACTIVE,
            data_source_id=uuid4(),
            scope_id=scope_id,
            space_id=space_id,
            physical_catalog="polaris",
            physical_namespace="sales",
            physical_table=name,
            source_key=f"polaris.sales.{name}",
            row_count=10,
            scope=ScopeSummary(
                id=scope_id,
                space_id=space_id,
                name="Ordering",
                slug="ordering",
                description=None,
            ),
            space=SpaceSummary(
                id=space_id,
                name="Commerce",
                slug="commerce",
                description=None,
            ),
            tags=[TagSummary(id=uuid4(), name="finance")],
        ),
        columns=[
            ColumnMetadata(
                id=selected_column,
                table_id=table_id,
                name="id",
                description=None,
                source_description=None,
                data_type="bigint",
                native_data_type=None,
                discovered_data_type=None,
                nullable=False,
                ordinal_position=0,
                is_primary_key=True,
                is_unique=True,
                is_foreign_key=foreign_key,
            )
        ],
        relationships=[
            TableRelation(
                id=uuid4(),
                source_table_id=table_id,
                target_table_id=target_table,
                relationship_type=RelationshipType.FOREIGN_KEY,
                origin=RelationshipOrigin.DATABASE_CONSTRAINT,
                status=RelationshipStatus.VERIFIED,
                description=None,
                column_mappings=[
                    ColumnMappingSummary(
                        id=uuid4(),
                        source_column_id=selected_column,
                        target_column_id=target_column,
                        position=0,
                    )
                ],
            )
        ],
    )


def _table_profile(
    table_id: UUID,
    column_id: UUID,
    samples: list[str] | None = None,
) -> TableDataProfile:
    return TableDataProfile(
        table_id=table_id,
        access_mode=DataAccessMode.SAMPLED,
        row_count=10,
        columns=[_column_profile(column_id, samples=samples)],
    )


def _column_profile(column_id: UUID, samples: list[str] | None) -> ColumnDataProfile:
    return ColumnDataProfile(
        column_id=column_id,
        row_count=10,
        null_count=1,
        null_ratio=0.1,
        distinct_count=4,
        uniqueness_ratio=0.4,
        minimum=1,
        maximum=9,
        detected_pattern=None,
        sample_values=samples,
    )


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _table(
    service: OpenRegistry,
    scope_id: UUID,
    data_source_id: UUID,
    name: str,
) -> UUID:
    return service.tables.create_table(
        scope_id,
        CatalogTableCreate(
            data_source_id=data_source_id,
            name=name,
            description="stored",
            table_type=TableKind.TABLE,
            physical_catalog="polaris",
            physical_namespace="sales",
            physical_table=name,
            source_ref=SourceRef(catalog="polaris", namespace="sales", table=name),
        ),
    ).id
