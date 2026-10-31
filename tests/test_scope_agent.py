"""Scope model output and the LLM relationship rows it replaces."""

import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryFeedbackPart,
    RetryPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.knowledge.models import (
    ColumnRelation,
    ColumnRelationKind,
    PipelineInput,
    PreviousScopeFindings,
    ScopeCatalogContext,
    ScopeContextProposal,
    ScopeProposal,
    ScopeTableSummary,
    TableKnowledgeInput,
    TableLink,
    TableLinkKind,
)
from intelegent_data_catalog.llm.scope_context import ScopeContextAgent
from intelegent_data_catalog.llm.scope_graph import ScopeSubgraphAgent
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
from tests.test_scope_graph import _context, _link


def test_scope_agent_retries_until_the_link_is_in_scope() -> None:
    """The prompt carries table knowledge and the previous build. A bad id is rejected."""
    base: ScopeCatalogContext = _context()
    orders: ScopeTableSummary = base.tables[0]
    customers: ScopeTableSummary = base.tables[1]
    relation: ColumnRelation = ColumnRelation(
        source_column_id=orders.columns[0].id,
        target_column_id=customers.columns[0].id,
        kind=ColumnRelationKind.REFERENCES,
        reason="customer_id points at the customer key",
    )
    kept: TableLink = _link(
        orders.id,
        customers.id,
        orders.columns[1].id,
        customers.columns[1].id,
        TableLinkKind.JOINS_ON,
    )
    context: ScopeCatalogContext = base.model_copy(
        update={
            "scope": base.scope.model_copy(update={"context": "Ordering covers checkout."}),
            "tables": [
                orders.model_copy(update={"context": "One row per order."}),
                customers,
            ],
            "table_knowledge": [
                TableKnowledgeInput(
                    table_id=orders.id,
                    column_relations=[relation],
                    source="both",
                )
            ],
            "previous": PreviousScopeFindings(summary="Old summary", links=[kept], built_at=None),
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
                            "target_table_id": "00000000-0000-0000-0000-000000000001",
                            "source_column_id": str(orders.columns[1].id),
                            "target_column_id": str(customers.columns[1].id),
                            "kind": "JOINS_ON",
                            "reason": "invented table",
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

    agent: ScopeSubgraphAgent = ScopeSubgraphAgent(
        FunctionModel(respond),
        max_requests=4,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    selected: ScopeProposal = agent.propose(context)
    assert selected.summary == "Orders and customers share a region."
    assert selected.links == [kept]
    assert "table_knowledge" in prompts[0]
    assert "previous_findings" in prompts[0]
    assert "Old summary" in prompts[0]
    assert "scope_context" in prompts[0]
    assert "Ordering covers checkout." in prompts[0]
    assert "One row per order." in prompts[0]


def test_scope_prompts_include_airflow_instance_context() -> None:
    """Scope text uses table names. The graph prompt keeps table ids."""
    base: ScopeCatalogContext = _context()
    orders: ScopeTableSummary = base.tables[0]
    customers: ScopeTableSummary = base.tables[1]
    outside: UUID = uuid4()
    pipeline: PipelineInput = PipelineInput(
        dag_row_id=uuid4(),
        dag_id="append_sales_order",
        instance_name="Iceberg",
        instance_context="Nightly sales loads.",
        description="Append one order for an existing customer.",
        reads=[customers.id],
        writes=[orders.id],
        unknown=[outside],
    )
    context: ScopeCatalogContext = base.model_copy(update={"pipelines": [pipeline]})
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        return ModelResponse(
            parts=[TextPart(content='{"context":"append_sales_order appends orders."}')]
        )

    text_agent: ScopeContextAgent = ScopeContextAgent(
        FunctionModel(respond),
        max_requests=2,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    written: ScopeContextProposal = text_agent.propose(context)
    assert written.context == "append_sales_order appends orders."
    text_pipeline: dict[str, object] = _pipeline_payload(prompts[0])
    assert text_pipeline["instance_context"] == "Nightly sales loads."
    assert text_pipeline["description"] == "Append one order for an existing customer."
    assert text_pipeline["reads"] == ["customers"]
    assert text_pipeline["writes"] == ["orders"]
    assert text_pipeline["unknown"] == []

    def graph_respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        return ModelResponse(
            parts=[TextPart(content='{"summary":"append_sales_order appends orders.","links":[]}')]
        )

    graph_agent: ScopeSubgraphAgent = ScopeSubgraphAgent(
        FunctionModel(graph_respond),
        max_requests=2,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    graph_agent.propose(context)
    graph_pipeline: dict[str, object] = _pipeline_payload(prompts[1])
    assert graph_pipeline["instance_context"] == "Nightly sales loads."
    assert graph_pipeline["description"] == "Append one order for an existing customer."
    assert graph_pipeline["reads"] == [str(customers.id)]
    assert graph_pipeline["writes"] == [str(orders.id)]
    assert graph_pipeline["unknown"] == [str(outside)]


def test_replace_keeps_non_llm_rows_and_rewrites_llm_rows(tmp_path: Path) -> None:
    """A rebuild deletes prior LLM relationships and leaves a declared foreign key."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        service: OpenRegistry = open_registry(session)
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
        scope_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        orders_id: UUID = _table(service, scope_id, source_id, "orders")
        customers_id: UUID = _table(service, scope_id, source_id, "customers")
        order_customer: UUID = service.tables.create_column(
            orders_id,
            ColumnCreate(name="customer_id", data_type="bigint", ordinal_position=0),
        ).id
        order_region: UUID = service.tables.create_column(
            orders_id,
            ColumnCreate(name="region", data_type="varchar", ordinal_position=1),
        ).id
        customer_id: UUID = service.tables.create_column(
            customers_id,
            ColumnCreate(name="id", data_type="bigint", ordinal_position=0, is_primary_key=True),
        ).id
        customer_region: UUID = service.tables.create_column(
            customers_id,
            ColumnCreate(name="region", data_type="varchar", ordinal_position=1),
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
        stale: TableLink = _link(orders_id, customers_id, order_region, customer_region)
        store.replace({orders_id, customers_id}, [stale])
        fresh: TableLink = _link(
            orders_id,
            customers_id,
            order_region,
            customer_region,
            TableLinkKind.JOINS_ON,
        )
        fresh = fresh.model_copy(update={"reason": "region is the join"})
        store.replace({orders_id, customers_id}, [fresh])
        rows: list[RelationshipRow] = RegistryRepository(session).list_relationships_for_table(
            orders_id
        )
        origins: set[str] = {row.origin for row in rows}
        assert RelationshipOrigin.DATABASE_CONSTRAINT.value in origins
        assert RelationshipOrigin.LLM.value in origins
        assert len(origins) == 2
        llm_rows: list[RelationshipRow] = [
            row for row in rows if row.origin == RelationshipOrigin.LLM.value
        ]
        assert len(llm_rows) == 1
        assert llm_rows[0].status == RelationshipStatus.UNVERIFIED.value
        assert llm_rows[0].relationship_type == RelationshipType.MANUAL.value
        assert llm_rows[0].description == "region is the join"
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
    seen_user: bool = False
    message: ModelMessage
    for message in messages:
        part: object
        for part in message.parts:
            if isinstance(part, RetryPromptPart | RetryFeedbackPart):
                chunks.append(str(part.content))
            elif isinstance(part, UserPromptPart):
                if seen_user:
                    chunks.append(str(part.content))
                seen_user = True
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
