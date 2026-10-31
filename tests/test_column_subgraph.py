"""Column subgraph written when a table context is built."""

import json
import logging
from pathlib import Path
from typing import LiteralString
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
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

from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    GraphUnavailableError,
    ValidationAppError,
)
from intelegent_data_catalog.graph.knowledge_subgraph import (
    KnowledgeEdgeWrite,
    KnowledgeSubgraphWrite,
    knowledge_statements,
)
from intelegent_data_catalog.knowledge.context import column_graph as column_graph_module
from intelegent_data_catalog.knowledge.context.column_graph import (
    ColumnProposalContext,
    ColumnSubgraphBuilder,
    ScopeColumnCatalog,
    accept_column_relations,
    assemble_column_subgraph,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnCandidate,
    ColumnDataProfile,
    ColumnRelation,
    ColumnRelationKind,
    DataAccessMode,
    LineageContext,
    LineageDataset,
    TableAnalysisContext,
    TableCatalogContext,
    TableDataProfile,
)
from intelegent_data_catalog.knowledge.operations.table_operation import TableAgent
from intelegent_data_catalog.llm.column_graph import ColumnSubgraphAgent
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import DataSourceType, TableKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    CatalogTableCreate,
    ColumnCreate,
    DataSourceCreate,
    ScopeCreate,
    SourceRef,
    SpaceCreate,
)
from tests.registry_factory import OpenRegistry, open_registry
from tests.test_knowledge_builder import _context, _factory


def test_build_context_records_column_relations() -> None:
    """A configured appender runs during context build and its relations come back."""
    table_id: UUID = uuid4()
    seen: list[UUID] = []

    class _Appender:
        def apply(self, context: TableAnalysisContext) -> TableAnalysisContext:
            seen.append(context.table.id)
            relation: ColumnRelation = ColumnRelation(
                source_column_id=context.columns[0].id,
                target_column_id=uuid4(),
                kind=ColumnRelationKind.REFERENCES,
                reason="customer id points at the customer key",
            )
            return context.model_copy(update={"column_relations": [relation]})

    agent: TableAgent = _factory(_context(table_id), column_graph=_Appender()).create(table_id)
    built: TableAnalysisContext = agent.build_context()
    assert seen == [table_id]
    assert len(built.column_relations) == 1
    assert built.column_relations[0].kind == ColumnRelationKind.REFERENCES


def test_accept_rejects_unknown_self_and_off_focus_links() -> None:
    """A relation must use two known columns and touch the focus table."""
    focus_table: UUID = uuid4()
    other_table: UUID = uuid4()
    focus_column: UUID = uuid4()
    left: UUID = uuid4()
    right: UUID = uuid4()
    columns: list[ColumnCandidate] = [
        _candidate(focus_column, focus_table, "orders", "customer_id", on_focus=True),
        _candidate(left, other_table, "customers", "id", on_focus=False),
        _candidate(right, other_table, "customers", "email", on_focus=False),
    ]
    with pytest.raises(ValidationAppError, match="unknown column"):
        accept_column_relations(
            focus_table,
            columns,
            [_relation(focus_column, uuid4(), ColumnRelationKind.REFERENCES)],
        )
    with pytest.raises(ValidationAppError, match="two columns"):
        accept_column_relations(
            focus_table,
            columns,
            [_relation(focus_column, focus_column, ColumnRelationKind.SAME_MEANING)],
        )
    with pytest.raises(ValidationAppError, match="focus table"):
        accept_column_relations(
            focus_table,
            columns,
            [_relation(left, right, ColumnRelationKind.JOINS_ON)],
        )


def test_accept_drops_duplicate_relations() -> None:
    """The same column pair and kind is written once."""
    focus_table: UUID = uuid4()
    source: UUID = uuid4()
    target: UUID = uuid4()
    columns: list[ColumnCandidate] = [
        _candidate(source, focus_table, "orders", "customer_id", on_focus=True),
        _candidate(target, uuid4(), "customers", "id", on_focus=False),
    ]
    relation: ColumnRelation = _relation(source, target, ColumnRelationKind.REFERENCES)
    accepted: list[ColumnRelation] = accept_column_relations(
        focus_table,
        columns,
        [relation, relation],
    )
    assert accepted == [relation]


def test_blank_reason_is_rejected() -> None:
    """A relation needs a reason the graph can store."""
    with pytest.raises(ValidationError):
        ColumnRelation(
            source_column_id=uuid4(),
            target_column_id=uuid4(),
            kind=ColumnRelationKind.REFERENCES,
            reason="   ",
        )


def test_assemble_writes_only_the_focus_table() -> None:
    """Another table's columns stay out. A scope agent owns those nodes."""
    focus_table: UUID = uuid4()
    other_table: UUID = uuid4()
    order_customer: UUID = uuid4()
    order_total: UUID = uuid4()
    customer_id: UUID = uuid4()
    customer_email: UUID = uuid4()
    columns: list[ColumnCandidate] = [
        _candidate(order_customer, focus_table, "orders", "customer_id", on_focus=True),
        _candidate(order_total, focus_table, "orders", "total", on_focus=True),
        _candidate(customer_id, other_table, "customers", "id", on_focus=False),
        _candidate(customer_email, other_table, "customers", "email", on_focus=False),
    ]
    cross_table: ColumnRelation = _relation(
        order_customer,
        customer_id,
        ColumnRelationKind.REFERENCES,
    )
    inside: ColumnRelation = _relation(
        order_customer,
        order_total,
        ColumnRelationKind.DERIVED_FROM,
    )
    document: KnowledgeSubgraphWrite = assemble_column_subgraph(
        focus_table,
        "orders",
        columns,
        [cross_table, inside],
    )
    names: set[str] = {node.name for node in document.columns}
    assert names == {"customer_id", "total"}
    assert "id" not in names
    assert "email" not in names
    table_names: set[str] = {node.name for node in document.tables}
    assert table_names == {"orders"}
    relation_edges: list[str] = [
        edge.kind for edge in document.edges if edge.kind != "HAS_COLUMN"
    ]
    assert relation_edges == ["DERIVED_FROM"]
    assert document.edges[-1].reason == inside.reason
    assert document.owner_table_id == str(focus_table)
    assert any(edge.kind == "HAS_COLUMN" for edge in document.edges)
    statements: list[tuple[LiteralString, dict[str, object]]] = knowledge_statements(document)
    delete_query: str = statements[0][0]
    assert "projection = 'knowledge'" in delete_query
    assert "DERIVED_FROM" in statements[-1][0]
    assert statements[-1][1]["reason"] == inside.reason
    assert statements[-1][1]["owner"] == str(focus_table)


def test_unknown_subgraph_relationship_is_rejected() -> None:
    """A relation kind outside the subgraph vocabulary is not sent to Neo4j."""
    document: KnowledgeSubgraphWrite = KnowledgeSubgraphWrite(
        owner_table_id=str(uuid4()),
        tables=[],
        columns=[],
        edges=[
            KnowledgeEdgeWrite(source="a", target="b", kind="GUESS", reason="no"),
        ],
    )
    with pytest.raises(GraphUnavailableError, match="unknown graph relationship"):
        knowledge_statements(document)


def test_builder_writes_model_relations_for_focus_columns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The builder validates the proposal, writes the subgraph, and returns the links."""
    caplog.set_level(logging.INFO, logger="intelegent_data_catalog.knowledge")
    focus_table: UUID = uuid4()
    other_table: UUID = uuid4()
    source: UUID = uuid4()
    target: UUID = uuid4()
    ignored: UUID = uuid4()
    columns: list[ColumnCandidate] = [
        _candidate(source, focus_table, "orders", "customer_id", on_focus=False),
        _candidate(target, other_table, "customers", "id", on_focus=False),
        _candidate(ignored, other_table, "customers", "email", on_focus=False),
    ]
    context: TableAnalysisContext = _analysis(
        focus_table,
        source,
        foreign_key=True,
    )
    seen: list[ColumnCandidate] = []
    written: list[KnowledgeSubgraphWrite] = []

    class _Source:
        def list_for_scope(
            self,
            scope_id: UUID,
            focus_table_id: UUID,
            extra_column_ids: set[UUID],
        ) -> list[ColumnCandidate]:
            assert scope_id == context.table.scope_id
            assert focus_table_id == focus_table
            assert source in extra_column_ids
            return columns

    class _Proposer:
        def propose(self, context: ColumnProposalContext) -> list[ColumnRelation]:
            assert context.analysis.table.id == focus_table
            seen.extend(context.columns)
            return [_relation(source, target, ColumnRelationKind.REFERENCES)]

    class _Writer:
        def write(self, document: KnowledgeSubgraphWrite) -> None:
            written.append(document)

    built: TableAnalysisContext = ColumnSubgraphBuilder(_Source(), _Proposer(), _Writer()).apply(
        context
    )
    assert built.column_relations[0].source_column_id == source
    assert f"kind={ColumnRelationKind.REFERENCES.value}" in caplog.text
    assert f"source={source}" in caplog.text
    assert f"target={target}" in caplog.text
    assert seen[0].is_foreign_key is True
    assert seen[0].on_focus_table is True
    assert {node.name for node in written[0].columns} == {"customer_id"}
    assert {node.name for node in written[0].tables} == {"orders"}
    assert all(edge.kind == "HAS_COLUMN" for edge in written[0].edges)


def test_builder_rejects_invented_columns_before_write() -> None:
    """An unknown column id never reaches Neo4j."""
    focus_table: UUID = uuid4()
    source: UUID = uuid4()
    context: TableAnalysisContext = _analysis(focus_table, source)
    writes: list[KnowledgeSubgraphWrite] = []

    class _Source:
        def list_for_scope(
            self,
            scope_id: UUID,
            focus_table_id: UUID,
            extra_column_ids: set[UUID],
        ) -> list[ColumnCandidate]:
            del scope_id, focus_table_id, extra_column_ids
            return [_candidate(source, focus_table, "orders", "customer_id", on_focus=True)]

    class _Proposer:
        def propose(self, context: ColumnProposalContext) -> list[ColumnRelation]:
            del context
            return [_relation(source, uuid4(), ColumnRelationKind.REFERENCES)]

    class _Writer:
        def write(self, document: KnowledgeSubgraphWrite) -> None:
            writes.append(document)

    with pytest.raises(ValidationAppError, match="unknown column"):
        ColumnSubgraphBuilder(_Source(), _Proposer(), _Writer()).apply(context)
    assert writes == []


def test_model_retries_an_invented_column_then_returns_the_link() -> None:
    """A column id outside the candidate list is sent back to the model."""
    focus_table: UUID = uuid4()
    source: UUID = uuid4()
    target: UUID = uuid4()
    context: TableAnalysisContext = _analysis(focus_table, source)
    context = context.model_copy(
        update={
            "table": context.table.model_copy(update={"context": "Orders placed by customers."}),
            "data_profile": TableDataProfile(
                table_id=focus_table,
                access_mode=DataAccessMode.PROFILE_ONLY,
                row_count=10,
                columns=[
                    ColumnDataProfile(
                        column_id=source,
                        uniqueness_ratio=0.2,
                        null_ratio=0.0,
                        distinct_count=4,
                    )
                ],
            ),
            "lineage": LineageContext(
                table_id=focus_table,
                upstream_datasets=[
                    LineageDataset(
                        namespace="commerce",
                        name="customers",
                        catalog_table_id=None,
                        resolved=False,
                    )
                ],
                downstream_datasets=[],
                producer_jobs=[],
                consumer_jobs=[],
            ),
        }
    )
    columns: list[ColumnCandidate] = [
        _candidate(source, focus_table, "orders", "customer_id", on_focus=True).model_copy(
            update={"context": "Customer who placed the order."}
        ),
        _candidate(target, uuid4(), "customers", "id", on_focus=False),
    ]
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        feedback: str = _feedback(messages)
        if feedback == "":
            body: str = json.dumps(
                {
                    "relations": [
                        {
                            "source_column_id": str(uuid4()),
                            "target_column_id": str(target),
                            "kind": "REFERENCES",
                            "reason": "invented source",
                        }
                    ]
                }
            )
            return ModelResponse(parts=[TextPart(content=body)])
        assert "unknown column" in feedback
        accepted: str = json.dumps(
            {
                "relations": [
                    {
                        "source_column_id": str(source),
                        "target_column_id": str(target),
                        "kind": "REFERENCES",
                        "reason": "customer_id points at the customer key",
                    }
                ]
            }
        )
        return ModelResponse(parts=[TextPart(content=accepted)])

    agent: ColumnSubgraphAgent = ColumnSubgraphAgent(
        FunctionModel(respond),
        max_requests=4,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    selected: list[ColumnRelation] = agent.propose(ColumnProposalContext(context, columns))
    assert selected[0].source_column_id == source
    assert selected[0].target_column_id == target
    assert selected[0].kind == ColumnRelationKind.REFERENCES
    assert "customer_id" in prompts[0]
    assert "customers" in prompts[0]
    assert "0.2" in prompts[0]
    assert "focus_table_context" in prompts[0]
    assert "Orders placed by customers." in prompts[0]
    assert "Customer who placed the order." in prompts[0]


def test_scope_catalog_prefers_focus_and_relationship_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The column cap keeps the focus column and a known key before sibling tables."""
    monkeypatch.setattr(column_graph_module, "_MAX_CANDIDATE_COLUMNS", 2)
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        service: OpenRegistry = open_registry(session)
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
        ordering_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id
        people_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="People")).id
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        orders_id: UUID = _table(service, ordering_id, source_id, "orders")
        payments_id: UUID = _table(service, ordering_id, source_id, "payments")
        customers_id: UUID = _table(service, people_id, source_id, "customers")
        order_customer: UUID = service.tables.create_column(
            orders_id,
            ColumnCreate(name="customer_id", data_type="bigint", ordinal_position=0),
        ).id
        service.tables.create_column(
            payments_id,
            ColumnCreate(name="payment_id", data_type="bigint", ordinal_position=0),
        )
        customer_key: UUID = service.tables.create_column(
            customers_id,
            ColumnCreate(name="id", data_type="bigint", ordinal_position=0, is_primary_key=True),
        ).id
        catalog: ScopeColumnCatalog = ScopeColumnCatalog(RegistryRepository(session))
        chosen: list[ColumnCandidate] = catalog.list_for_scope(
            ordering_id,
            orders_id,
            {customer_key},
        )
        assert [item.name for item in chosen] == ["customer_id", "id"]
        assert chosen[0].column_id == order_customer
        assert chosen[0].on_focus_table is True
        assert chosen[1].table_id == customers_id
        assert chosen[1].on_focus_table is False
    finally:
        session.close()
        database.dispose()


def test_scope_catalog_missing_table_is_not_found(tmp_path: Path) -> None:
    """A missing focus table does not invent columns."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        with pytest.raises(EntityNotFoundError):
            ScopeColumnCatalog(RegistryRepository(session)).list_for_scope(uuid4(), uuid4(), set())
    finally:
        session.close()
        database.dispose()


def _relation(source: UUID, target: UUID, kind: ColumnRelationKind) -> ColumnRelation:
    return ColumnRelation(
        source_column_id=source,
        target_column_id=target,
        kind=kind,
        reason="customer_id points at the customer key",
    )


def _candidate(
    column_id: UUID,
    table_id: UUID,
    table_name: str,
    name: str,
    *,
    on_focus: bool,
) -> ColumnCandidate:
    return ColumnCandidate(
        column_id=column_id,
        table_id=table_id,
        table_name=table_name,
        name=name,
        data_type="bigint",
        is_primary_key=name == "id",
        is_unique=name == "id",
        is_foreign_key=False,
        on_focus_table=on_focus,
        description=None,
    )


def _analysis(
    table_id: UUID,
    column_id: UUID,
    *,
    foreign_key: bool = False,
) -> TableAnalysisContext:
    catalog: TableCatalogContext = _context(table_id, column_id=column_id, foreign_key=foreign_key)
    return TableAnalysisContext(
        table=catalog.table,
        columns=catalog.columns,
        data_profile=None,
        existing_relationships=catalog.relationships,
        graph_neighbors=[],
        lineage=None,
    )


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
