"""Scope link checks and the scope-owned subgraph."""

from typing import LiteralString
from uuid import UUID, uuid4

import pytest

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.graph.knowledge_subgraph import (
    KnowledgeEdgeWrite,
    KnowledgeSubgraphWrite,
    knowledge_statements,
)
from intelegent_data_catalog.knowledge.context import scope_graph as scope_graph_module
from intelegent_data_catalog.knowledge.context.scope_graph import (
    ScopeGraphBuilder,
    accept_table_links,
    assemble_scope_subgraph,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnMappingSummary,
    ColumnMetadata,
    PipelineInput,
    PreviousScopeFindings,
    ScopeAnalysisContext,
    ScopeCatalogContext,
    ScopeProposal,
    ScopeSummary,
    ScopeTableSummary,
    SpaceSummary,
    TableLink,
    TableLinkKind,
    TableRelation,
)
from intelegent_data_catalog.persistence.enums import (
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    TableStatus,
)


def test_accept_rejects_links_outside_the_scope() -> None:
    """A link must use two different scope tables and their own columns."""
    context: ScopeCatalogContext = _context()
    orders: ScopeTableSummary = context.tables[0]
    customers: ScopeTableSummary = context.tables[1]
    order_column: UUID = orders.columns[0].id
    customer_column: UUID = customers.columns[0].id
    with pytest.raises(ValidationAppError, match="unknown table"):
        accept_table_links(context, [_link(uuid4(), customers.id, order_column, customer_column)])
    with pytest.raises(ValidationAppError, match="two tables"):
        accept_table_links(context, [_link(orders.id, orders.id, order_column, order_column)])
    with pytest.raises(ValidationAppError, match="source column"):
        accept_table_links(
            context,
            [_link(orders.id, customers.id, customer_column, customer_column)],
        )


def test_accept_rejects_a_catalog_pair_and_extra_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared relationship stays put. The link cap is enforced."""
    monkeypatch.setattr(scope_graph_module, "_MAX_LINKS", 1)
    context: ScopeCatalogContext = _context()
    orders: ScopeTableSummary = context.tables[0]
    customers: ScopeTableSummary = context.tables[1]
    with pytest.raises(ValidationAppError, match="already a catalog relationship"):
        accept_table_links(
            context,
            [
                _link(
                    orders.id,
                    customers.id,
                    orders.columns[0].id,
                    customers.columns[0].id,
                )
            ],
        )
    extra: TableLink = _link(
        orders.id,
        customers.id,
        orders.columns[1].id,
        customers.columns[1].id,
        TableLinkKind.JOINS_ON,
    )
    another: TableLink = _link(
        customers.id,
        orders.id,
        customers.columns[1].id,
        orders.columns[1].id,
        TableLinkKind.JOINS_ON,
    )
    with pytest.raises(ValidationAppError, match="too many table links"):
        accept_table_links(context, [extra, another])


def test_accept_keeps_one_copy_of_a_new_link() -> None:
    """The same proposal twice becomes one link. An LLM row does not block it."""
    context: ScopeCatalogContext = _context()
    orders: ScopeTableSummary = context.tables[0]
    customers: ScopeTableSummary = context.tables[1]
    link: TableLink = _link(
        orders.id,
        customers.id,
        orders.columns[1].id,
        customers.columns[1].id,
        TableLinkKind.JOINS_ON,
    )
    accepted: list[TableLink] = accept_table_links(context, [link, link])
    assert accepted == [link]


def test_assemble_writes_scope_table_nodes_only() -> None:
    """Catalog relationships and accepted links are table edges. Columns stay off."""
    context: ScopeCatalogContext = _context()
    orders: ScopeTableSummary = context.tables[0]
    customers: ScopeTableSummary = context.tables[1]
    link: TableLink = _link(
        orders.id,
        customers.id,
        orders.columns[1].id,
        customers.columns[1].id,
        TableLinkKind.JOINS_ON,
    )
    document: KnowledgeSubgraphWrite = assemble_scope_subgraph(
        context.scope.id,
        context.tables,
        context.relationships,
        [link],
    )
    assert document.owner_kind == "scope"
    assert document.owner_table_id == str(context.scope.id)
    assert document.columns == []
    assert {node.name for node in document.tables} == {"orders", "customers"}
    assert document.tables[0].node_id.startswith(f"knowledge:scope:{context.scope.id}:table:")
    kinds: list[str] = [edge.kind for edge in document.edges]
    assert kinds == ["REFERENCES", "JOINS_ON"]
    assert document.edges[0].reason == "catalog DATABASE_CONSTRAINT"
    assert "orders.region -> customers.region" in (document.edges[1].reason or "")
    statements: list[tuple[LiteralString, dict[str, object]]] = knowledge_statements(document)
    assert "owner_scope_id" in statements[0][0]
    assert "JOINS_ON" in statements[-1][0]


def test_assemble_writes_pipeline_read_to_write_edges() -> None:
    """A DAG that reads one scope table and writes another adds one DERIVED_FROM edge."""
    context: ScopeCatalogContext = _context()
    orders: ScopeTableSummary = context.tables[0]
    customers: ScopeTableSummary = context.tables[1]
    outside: UUID = uuid4()
    first: PipelineInput = PipelineInput(
        dag_row_id=uuid4(),
        dag_id="append_sales_order",
        instance_name="Iceberg",
        instance_context="Nightly sales loads.",
        reads=[customers.id, outside],
        writes=[orders.id],
    )
    second: PipelineInput = PipelineInput(
        dag_row_id=uuid4(),
        dag_id="append_sales_customer",
        instance_name="Iceberg",
        reads=[customers.id],
        writes=[orders.id, customers.id],
    )
    document: KnowledgeSubgraphWrite = assemble_scope_subgraph(
        context.scope.id,
        context.tables,
        [],
        [],
        [first, second],
    )
    derived: list[str] = [edge.kind for edge in document.edges]
    assert derived == ["DERIVED_FROM"]
    written: KnowledgeEdgeWrite = document.edges[0]
    assert written.source == f"knowledge:scope:{context.scope.id}:table:{customers.id}"
    assert written.target == f"knowledge:scope:{context.scope.id}:table:{orders.id}"
    assert written.reason == "Iceberg append_sales_order; Iceberg append_sales_customer"


def test_builder_counts_links_kept_from_the_previous_build() -> None:
    """A repeated column pair from the previous scope build is counted."""
    context: ScopeCatalogContext = _context()
    orders: ScopeTableSummary = context.tables[0]
    customers: ScopeTableSummary = context.tables[1]
    kept: TableLink = _link(
        orders.id,
        customers.id,
        orders.columns[1].id,
        customers.columns[1].id,
    )
    context = context.model_copy(
        update={
            "previous": PreviousScopeFindings(summary="Old", links=[kept], built_at=None),
        }
    )
    writes: list[KnowledgeSubgraphWrite] = []
    replaced: list[list[TableLink]] = []

    class _Proposer:
        def propose(self, proposed_context: ScopeCatalogContext) -> ScopeProposal:
            del proposed_context
            return ScopeProposal(summary="Orders belong to customers.", links=[kept])

    class _Writer:
        def write(self, document: KnowledgeSubgraphWrite) -> None:
            writes.append(document)

    class _Relationships:
        def replace(self, scope_table_ids: set[UUID], links: list[TableLink]) -> None:
            del scope_table_ids
            replaced.append(links)

    class _Publisher:
        def __init__(self) -> None:
            self.calls: int = 0

        def publish(self) -> None:
            self.calls += 1

    publisher: _Publisher = _Publisher()
    built: ScopeAnalysisContext = ScopeGraphBuilder(
        _Proposer(),
        _Writer(),
        _Relationships(),
        publisher,
    ).apply(context)
    assert built.summary == "Orders belong to customers."
    assert built.table_links == [kept]
    assert built.previous_links_kept == 1
    assert replaced == [[kept]]
    assert writes[0].owner_kind == "scope"
    assert publisher.calls == 1


def _context() -> ScopeCatalogContext:
    scope_id: UUID = uuid4()
    space_id: UUID = uuid4()
    orders_id: UUID = uuid4()
    customers_id: UUID = uuid4()
    order_customer: UUID = uuid4()
    order_region: UUID = uuid4()
    customer_id: UUID = uuid4()
    customer_region: UUID = uuid4()
    orders: ScopeTableSummary = _table(
        orders_id,
        "orders",
        [
            _column(order_customer, orders_id, "customer_id"),
            _column(order_region, orders_id, "region"),
        ],
    )
    customers: ScopeTableSummary = _table(
        customers_id,
        "customers",
        [
            _column(customer_id, customers_id, "id"),
            _column(customer_region, customers_id, "region"),
        ],
    )
    relationship: TableRelation = TableRelation(
        id=uuid4(),
        source_table_id=orders_id,
        target_table_id=customers_id,
        relationship_type=RelationshipType.FOREIGN_KEY,
        origin=RelationshipOrigin.DATABASE_CONSTRAINT,
        status=RelationshipStatus.VERIFIED,
        description=None,
        column_mappings=[
            ColumnMappingSummary(
                id=uuid4(),
                source_column_id=order_customer,
                target_column_id=customer_id,
                position=0,
            )
        ],
    )
    return ScopeCatalogContext(
        scope=ScopeSummary(
            id=scope_id,
            space_id=space_id,
            name="Ordering",
            slug="ordering",
            description="Sales tables",
        ),
        space=SpaceSummary(
            id=space_id,
            name="Commerce",
            slug="commerce",
            description=None,
        ),
        tables=[orders, customers],
        relationships=[relationship],
        table_knowledge=[],
        tables_without_context=[],
        previous=None,
    )


def _table(table_id: UUID, name: str, columns: list[ColumnMetadata]) -> ScopeTableSummary:
    return ScopeTableSummary(
        id=table_id,
        name=name,
        display_name=None,
        description=None,
        status=TableStatus.ACTIVE,
        row_count=None,
        columns=columns,
    )


def _column(column_id: UUID, table_id: UUID, name: str) -> ColumnMetadata:
    return ColumnMetadata(
        id=column_id,
        table_id=table_id,
        name=name,
        description=None,
        source_description=None,
        data_type="bigint",
        native_data_type=None,
        discovered_data_type=None,
        nullable=True,
        ordinal_position=0,
        is_primary_key=name == "id",
        is_unique=name == "id",
        is_foreign_key=False,
    )


def _link(
    source_table: UUID,
    target_table: UUID,
    source_column: UUID,
    target_column: UUID,
    kind: TableLinkKind = TableLinkKind.REFERENCES,
) -> TableLink:
    return TableLink(
        source_table_id=source_table,
        target_table_id=target_table,
        source_column_id=source_column,
        target_column_id=target_column,
        kind=kind,
        reason="shared region",
    )
