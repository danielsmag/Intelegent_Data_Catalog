"""Space model output and the cross-scope LLM rows it replaces."""

import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.knowledge.models import (
    PipelineInput,
    PreviousSpaceFindings,
    ScopeGraphEdge,
    ScopeKnowledgeInput,
    ScopeTableSummary,
    SpaceCatalogContext,
    SpaceContextProposal,
    SpaceProposal,
    TableLink,
    TableLinkKind,
)
from intelegent_data_catalog.llm.space_context import SpaceContextAgent
from intelegent_data_catalog.llm.space_graph import SpaceSubgraphAgent
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    DataSourceType,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    TableKind,
)
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import RelationshipRow
from intelegent_data_catalog.registry.llm_relationships import LlmRelationshipStore
from intelegent_data_catalog.registry.repository import RegistryRepository
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
from tests.test_scope_graph import _link
from tests.test_space_graph import _context


def test_space_agent_retries_a_same_scope_link() -> None:
    """The prompt carries scope graphs. A link inside one scope is rejected."""
    base: SpaceCatalogContext = _context()
    orders = base.scopes[0].tables[0]
    invoices = base.scopes[0].tables[1]
    customers = base.scopes[1].tables[0]
    kept: TableLink = _link(
        orders.id,
        customers.id,
        orders.columns[1].id,
        customers.columns[1].id,
        TableLinkKind.JOINS_ON,
    )
    context: SpaceCatalogContext = base.model_copy(
        update={
            "space": base.space.model_copy(update={"context": "Commerce covers sales."}),
            "scope_knowledge": [
                ScopeKnowledgeInput(
                    scope_id=base.scopes[0].id,
                    summary="Ordering joins orders to invoices.",
                    links=[],
                    graph=[
                        ScopeGraphEdge(
                            source_table_id=orders.id,
                            target_table_id=invoices.id,
                            kind=TableLinkKind.JOINS_ON,
                            reason="shared region inside ordering",
                        )
                    ],
                    source="both",
                )
            ],
            "previous": PreviousSpaceFindings(summary="Old summary", links=[kept], built_at=None),
        }
    )
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        if _feedback(messages) == "":
            body: str = json.dumps(
                {
                    "summary": "Not yet.",
                    "links": [
                        {
                            "source_table_id": str(orders.id),
                            "target_table_id": str(invoices.id),
                            "source_column_id": str(orders.columns[1].id),
                            "target_column_id": str(invoices.columns[0].id),
                            "kind": "JOINS_ON",
                            "reason": "same scope",
                        }
                    ],
                }
            )
            return ModelResponse(parts=[TextPart(content=body)])
        accepted: str = json.dumps(
            {
                "summary": "Orders and customers share a region.",
                "links": [
                    {
                        "source_table_id": str(kept.source_table_id),
                        "target_table_id": str(kept.target_table_id),
                        "source_column_id": str(kept.source_column_id),
                        "target_column_id": str(kept.target_column_id),
                        "kind": "JOINS_ON",
                        "reason": "shared region",
                    }
                ],
            }
        )
        return ModelResponse(parts=[TextPart(content=accepted)])

    agent: SpaceSubgraphAgent = SpaceSubgraphAgent(
        FunctionModel(respond),
        max_requests=4,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    selected: SpaceProposal = agent.propose(context)
    assert selected.summary == "Orders and customers share a region."
    assert selected.links == [kept]
    assert "scope_knowledge" in prompts[0]
    assert "previous_findings" in prompts[0]
    assert "Old summary" in prompts[0]
    assert "Commerce covers sales." in prompts[0]
    assert "shared region inside ordering" in prompts[0]


def test_space_summary_must_name_attached_pipelines() -> None:
    """A summary that ignores an attached DAG is sent back until the dag id is named."""
    base: SpaceCatalogContext = _context()
    pipeline: PipelineInput = PipelineInput(
        dag_row_id=uuid4(),
        dag_id="append_sales_order",
        instance_name="Iceberg",
        instance_context="Nightly sales loads.",
    )
    context: SpaceCatalogContext = base.model_copy(update={"pipelines": [pipeline]})
    prompts: list[str] = []
    feedback: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        feedback.append(_feedback(messages))
        if _feedback(messages) == "":
            return ModelResponse(
                parts=[TextPart(content='{"summary":"No cross-scope links.","links":[]}')]
            )
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        '{"summary":"append_sales_order appends orders for Iceberg.","links":[]}'
                    )
                )
            ]
        )

    agent: SpaceSubgraphAgent = SpaceSubgraphAgent(
        FunctionModel(respond),
        max_requests=4,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    selected: SpaceProposal = agent.propose(context)
    assert selected.summary == "append_sales_order appends orders for Iceberg."
    assert "Nightly sales loads." in prompts[0]
    assert "append_sales_order" in feedback[1]


def test_space_prompts_include_airflow_table_directions() -> None:
    """Space text uses table names. The graph prompt keeps table ids."""
    base: SpaceCatalogContext = _context()
    orders: ScopeTableSummary = base.scopes[0].tables[0]
    invoices: ScopeTableSummary = base.scopes[0].tables[1]
    customers: ScopeTableSummary = base.scopes[1].tables[0]
    outside: UUID = uuid4()
    pipeline: PipelineInput = PipelineInput(
        dag_row_id=uuid4(),
        dag_id="append_sales_order",
        instance_name="Iceberg",
        instance_context="Nightly sales loads.",
        description="Append one order for an existing customer.",
        reads=[customers.id],
        writes=[orders.id],
        unknown=[invoices.id, outside],
    )
    context: SpaceCatalogContext = base.model_copy(update={"pipelines": [pipeline]})
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        return ModelResponse(
            parts=[TextPart(content='{"context":"append_sales_order appends orders."}')]
        )

    text_agent: SpaceContextAgent = SpaceContextAgent(
        FunctionModel(respond),
        max_requests=2,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    written: SpaceContextProposal = text_agent.propose(context)
    assert written.context == "append_sales_order appends orders."
    text_pipeline: dict[str, object] = _pipeline_payload(prompts[0])
    assert text_pipeline["description"] == "Append one order for an existing customer."
    assert text_pipeline["reads"] == ["customers"]
    assert text_pipeline["writes"] == ["orders"]
    assert text_pipeline["unknown"] == ["invoices"]

    def graph_respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        return ModelResponse(
            parts=[TextPart(content='{"summary":"append_sales_order appends orders.","links":[]}')]
        )

    graph_agent: SpaceSubgraphAgent = SpaceSubgraphAgent(
        FunctionModel(graph_respond),
        max_requests=2,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    graph_agent.propose(context)
    graph_pipeline: dict[str, object] = _pipeline_payload(prompts[1])
    assert graph_pipeline["description"] == "Append one order for an existing customer."
    assert graph_pipeline["reads"] == [str(customers.id)]
    assert graph_pipeline["writes"] == [str(orders.id)]
    assert graph_pipeline["unknown"] == [str(invoices.id), str(outside)]


def test_replace_across_scopes_keeps_intra_scope_rows(tmp_path: Path) -> None:
    """A space rebuild drops cross-scope LLM rows and leaves scope-agent rows."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        service: OpenRegistry = open_registry(session)
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
        ordering_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id
        billing_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Billing")).id
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        orders_id: UUID = _table(service, ordering_id, source_id, "orders")
        invoices_id: UUID = _table(service, ordering_id, source_id, "invoices")
        customers_id: UUID = _table(service, billing_id, source_id, "customers")
        order_region: UUID = service.tables.create_column(
            orders_id,
            ColumnCreate(name="region", data_type="varchar", ordinal_position=0),
        ).id
        invoice_region: UUID = service.tables.create_column(
            invoices_id,
            ColumnCreate(name="region", data_type="varchar", ordinal_position=0),
        ).id
        customer_region: UUID = service.tables.create_column(
            customers_id,
            ColumnCreate(name="region", data_type="varchar", ordinal_position=0),
        ).id
        customer_id: UUID = service.tables.create_column(
            customers_id,
            ColumnCreate(name="id", data_type="bigint", ordinal_position=1, is_primary_key=True),
        ).id
        order_customer: UUID = service.tables.create_column(
            orders_id,
            ColumnCreate(name="customer_id", data_type="bigint", ordinal_position=1),
        ).id
        service.relationships.create_relationship(
            RelationshipCreate(
                source_table_id=orders_id,
                target_table_id=customers_id,
                relationship_type=RelationshipType.FOREIGN_KEY,
                origin=RelationshipOrigin.DATABASE_CONSTRAINT,
                status=RelationshipStatus.VERIFIED,
                column_mappings=[
                    ColumnMappingWrite(
                        source_column_id=order_customer,
                        target_column_id=customer_id,
                        position=0,
                    )
                ],
            )
        )
        store: LlmRelationshipStore = LlmRelationshipStore(session)
        inside: TableLink = _link(orders_id, invoices_id, order_region, invoice_region)
        store.replace({orders_id, invoices_id}, [inside])
        stale: TableLink = _link(orders_id, customers_id, order_region, customer_region)
        scope_of: dict[UUID, UUID] = {
            orders_id: ordering_id,
            invoices_id: ordering_id,
            customers_id: billing_id,
        }
        store.replace_across_scopes(scope_of, [stale])
        fresh: TableLink = _link(
            orders_id,
            customers_id,
            order_region,
            customer_region,
            TableLinkKind.JOINS_ON,
        )
        fresh = fresh.model_copy(update={"reason": "region crosses scopes"})
        store.replace_across_scopes(scope_of, [fresh])
        rows: list[RelationshipRow] = RegistryRepository(session).list_relationships_for_table(
            orders_id
        )
        llm_rows: list[RelationshipRow] = [
            row for row in rows if row.origin == RelationshipOrigin.LLM.value
        ]
        assert len(llm_rows) == 2
        descriptions: set[str | None] = {row.description for row in llm_rows}
        assert "shared region" in descriptions
        assert "region crosses scopes" in descriptions
        assert any(row.origin == RelationshipOrigin.DATABASE_CONSTRAINT.value for row in rows)
        cross: list[RelationshipRow] = [
            row
            for row in llm_rows
            if {row.source_table_id, row.target_table_id} == {orders_id, customers_id}
        ]
        assert len(cross) == 1
        assert cross[0].relationship_type == RelationshipType.MANUAL.value
    finally:
        session.close()
        database.dispose()


def _pipeline_payload(prompt: str) -> dict[str, object]:
    body: object = json.loads(prompt)
    assert isinstance(body, dict)
    pipelines: object = body["pipelines"]
    assert isinstance(pipelines, list)
    pipeline: object = pipelines[0]
    assert isinstance(pipeline, dict)
    return pipeline


def _user_text(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    message: ModelMessage
    for message in messages:
        part: object
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                chunks.append(str(part.content))
    return "\n".join(chunks)


def _feedback(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    message: ModelMessage
    for message in messages:
        part: object
        for part in message.parts:
            if isinstance(part, RetryPromptPart):
                chunks.append(str(part.content))
    return "\n".join(chunks)


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _table(service: OpenRegistry, scope_id: UUID, source_id: UUID, name: str) -> UUID:
    return service.tables.create_table(
        scope_id,
        CatalogTableCreate(
            data_source_id=source_id,
            name=name,
            table_type=TableKind.TABLE,
            physical_catalog="polaris",
            physical_namespace="sales",
            physical_table=name,
            source_ref=SourceRef(catalog="polaris", namespace="sales", table=name),
        ),
    ).id
