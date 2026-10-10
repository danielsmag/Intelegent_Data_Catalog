"""Space link checks and the space-owned subgraph."""

from uuid import UUID, uuid4

import pytest

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.graph.knowledge_subgraph import (
    KnowledgeSubgraphWrite,
    knowledge_statements,
)
from intelegent_data_catalog.knowledge.context import space_graph as space_graph_module
from intelegent_data_catalog.knowledge.context.space_graph import (
    SpaceGraphBuilder,
    accept_space_links,
    assemble_space_subgraph,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnMappingSummary,
    PreviousSpaceFindings,
    ScopeTableSummary,
    SpaceAnalysisContext,
    SpaceCatalogContext,
    SpaceProposal,
    SpaceScopeSummary,
    SpaceSummary,
    TableLink,
    TableLinkKind,
    TableRelation,
)
from intelegent_data_catalog.persistence.enums import (
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from tests.test_scope_graph import _column, _link, _table


def test_accept_rejects_a_same_scope_link_and_an_unknown_table() -> None:
    """A space link must join two known tables from different scopes."""
    context: SpaceCatalogContext = _context()
    orders: ScopeTableSummary = context.scopes[0].tables[0]
    invoices: ScopeTableSummary = context.scopes[0].tables[1]
    customers: ScopeTableSummary = context.scopes[1].tables[0]
    with pytest.raises(ValidationAppError, match="unknown table"):
        accept_space_links(
            context,
            [_link(uuid4(), customers.id, orders.columns[0].id, customers.columns[0].id)],
        )
    with pytest.raises(ValidationAppError, match="cross scopes"):
        accept_space_links(
            context,
            [
                _link(
                    orders.id,
                    invoices.id,
                    orders.columns[1].id,
                    invoices.columns[0].id,
                )
            ],
        )


def test_accept_rejects_a_catalog_pair_and_extra_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared cross-scope relationship stays put. The link cap is enforced."""
    monkeypatch.setattr(space_graph_module, "_MAX_LINKS", 1)
    context: SpaceCatalogContext = _context()
    orders: ScopeTableSummary = context.scopes[0].tables[0]
    customers: ScopeTableSummary = context.scopes[1].tables[0]
    with pytest.raises(ValidationAppError, match="already a catalog relationship"):
        accept_space_links(
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
        accept_space_links(context, [extra, another])


def test_assemble_writes_scope_and_table_nodes() -> None:
    """The space subgraph holds scopes, tables, and cross-scope edges."""
    context: SpaceCatalogContext = _context()
    orders: ScopeTableSummary = context.scopes[0].tables[0]
    customers: ScopeTableSummary = context.scopes[1].tables[0]
    link: TableLink = _link(
        orders.id,
        customers.id,
        orders.columns[1].id,
        customers.columns[1].id,
        TableLinkKind.JOINS_ON,
    )
    document: KnowledgeSubgraphWrite = assemble_space_subgraph(
        context.space.id,
        context.scopes,
        context.relationships,
        [link],
    )
    assert document.owner_kind == "space"
    assert document.owner_table_id == str(context.space.id)
    assert document.columns == []
    assert {node.name for node in document.scopes} == {"Ordering", "Billing"}
    assert {node.name for node in document.tables} == {"orders", "invoices", "customers"}
    assert document.scopes[0].node_id.startswith(f"knowledge:space:{context.space.id}:scope:")
    kinds: list[str] = [edge.kind for edge in document.edges]
    assert kinds.count("HAS_TABLE") == 3
    assert "REFERENCES" in kinds
    assert "JOINS_ON" in kinds
    statements: list[tuple[object, dict[str, object]]] = knowledge_statements(document)
    assert "owner_space_id" in str(statements[0][0])
    assert any("CREATE (n:Scope" in str(query) for query, _parameters in statements)
    assert any("HAS_TABLE" in str(query) for query, _parameters in statements)


def test_builder_counts_links_kept_from_the_previous_build() -> None:
    """A repeated column pair from the previous space build is counted."""
    context: SpaceCatalogContext = _context()
    orders: ScopeTableSummary = context.scopes[0].tables[0]
    customers: ScopeTableSummary = context.scopes[1].tables[0]
    kept: TableLink = _link(
        orders.id,
        customers.id,
        orders.columns[1].id,
        customers.columns[1].id,
        TableLinkKind.JOINS_ON,
    )
    context = context.model_copy(
        update={
            "previous": PreviousSpaceFindings(summary="Old", links=[kept], built_at=None),
        }
    )
    writes: list[KnowledgeSubgraphWrite] = []
    replaced: list[list[TableLink]] = []

    class _Proposer:
        def propose(self, proposed_context: SpaceCatalogContext) -> SpaceProposal:
            del proposed_context
            return SpaceProposal(summary="Orders belong to customers.", links=[kept])

    class _Writer:
        def write(self, document: KnowledgeSubgraphWrite) -> None:
            writes.append(document)

    class _Relationships:
        def replace_across_scopes(self, scope_of: dict[UUID, UUID], links: list[TableLink]) -> None:
            del scope_of
            replaced.append(links)

    class _Publisher:
        def __init__(self) -> None:
            self.calls: int = 0

        def publish(self) -> None:
            self.calls += 1

    publisher: _Publisher = _Publisher()
    built: SpaceAnalysisContext = SpaceGraphBuilder(
        _Proposer(),
        _Writer(),
        _Relationships(),
        publisher,
    ).apply(context)
    assert built.summary == "Orders belong to customers."
    assert built.table_links == [kept]
    assert built.previous_links_kept == 1
    assert replaced == [[kept]]
    assert writes[0].owner_kind == "space"
    assert publisher.calls == 1


def _context() -> SpaceCatalogContext:
    space_id: UUID = uuid4()
    ordering_id: UUID = uuid4()
    billing_id: UUID = uuid4()
    orders_id: UUID = uuid4()
    invoices_id: UUID = uuid4()
    customers_id: UUID = uuid4()
    order_customer: UUID = uuid4()
    order_region: UUID = uuid4()
    invoice_region: UUID = uuid4()
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
    invoices: ScopeTableSummary = _table(
        invoices_id,
        "invoices",
        [_column(invoice_region, invoices_id, "region")],
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
    return SpaceCatalogContext(
        space=SpaceSummary(
            id=space_id,
            name="Commerce",
            slug="commerce",
            description="Sales",
        ),
        scopes=[
            SpaceScopeSummary(
                id=ordering_id,
                name="Ordering",
                slug="ordering",
                description=None,
                tables=[orders, invoices],
            ),
            SpaceScopeSummary(
                id=billing_id,
                name="Billing",
                slug="billing",
                description=None,
                tables=[customers],
            ),
        ],
        relationships=[relationship],
        scope_knowledge=[],
        scopes_without_context=[],
        previous=None,
    )
