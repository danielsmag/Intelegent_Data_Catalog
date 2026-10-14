"""Read model for one table's Neo4j knowledge subgraph."""

from intelegent_data_catalog.graph.document import GraphView
from intelegent_data_catalog.graph.knowledge_subgraph import (
    KnowledgeGraphLink,
    KnowledgeGraphNode,
    knowledge_graph_view,
    knowledge_link_from_row,
    knowledge_node_from_row,
)


def test_knowledge_graph_view_keeps_columns_and_same_meaning() -> None:
    """Tables, columns, and meaning edges survive. A dangling edge does not."""
    nodes: list[KnowledgeGraphNode] = [
        KnowledgeGraphNode(
            node_id="table-orders",
            catalog_id="orders",
            name="orders",
            kind="table",
        ),
        KnowledgeGraphNode(
            node_id="column-customer",
            catalog_id="customer-id",
            name="customer_id",
            kind="column",
        ),
        KnowledgeGraphNode(
            node_id="column-id",
            catalog_id="id",
            name="id",
            kind="column",
        ),
    ]
    links: list[KnowledgeGraphLink] = [
        KnowledgeGraphLink(source="table-orders", target="column-customer", kind="HAS_COLUMN"),
        KnowledgeGraphLink(
            source="column-customer",
            target="column-id",
            kind="SAME_MEANING",
            reason="both name the customer",
        ),
        KnowledgeGraphLink(source="column-customer", target="missing", kind="REFERENCES"),
        KnowledgeGraphLink(source="table-orders", target="column-customer", kind="HAS_COLUMN"),
    ]
    view: GraphView = knowledge_graph_view(nodes, links)
    assert [node.kind for node in view.nodes] == ["table", "column", "column"]
    assert [(edge.kind, edge.style, edge.reason) for edge in view.edges] == [
        ("HAS_COLUMN", "hierarchy", None),
        ("SAME_MEANING", "relationship", "both name the customer"),
    ]
    assert view.truncated is False


def test_knowledge_node_from_row_reads_table_and_column_labels() -> None:
    """Neo4j labels become table and column kinds. Other labels are ignored."""
    table: KnowledgeGraphNode | None = knowledge_node_from_row(
        " knowledge:orders ",
        " orders ",
        " orders ",
        True,
        ["Table"],
    )
    column: KnowledgeGraphNode | None = knowledge_node_from_row(
        "knowledge:city",
        None,
        None,
        None,
        ["Column"],
    )
    assert table is not None
    assert table.kind == "table"
    assert table.node_id == "knowledge:orders"
    assert table.name == "orders"
    assert column is not None
    assert column.kind == "column"
    assert column.name == "knowledge:city"
    assert column.resolved is True
    dag: KnowledgeGraphNode | None = knowledge_node_from_row(
        "dag-append",
        "dag-row",
        "append_sales_order",
        True,
        ["Dag"],
    )
    assert dag is not None
    assert dag.kind == "dag"
    assert knowledge_node_from_row("job", "job", "load", True, ["Job"]) is None
    assert knowledge_node_from_row("", "id", "name", True, ["Table"]) is None


def test_knowledge_link_from_row_keeps_reason() -> None:
    """A relationship row keeps its type and reason."""
    link: KnowledgeGraphLink | None = knowledge_link_from_row(
        "column-a",
        "column-b",
        "SAME_MEANING",
        " same customer ",
    )
    assert link is not None
    assert link.kind == "SAME_MEANING"
    assert link.reason == "same customer"
    assert knowledge_link_from_row("a", None, "HAS_COLUMN", None) is None
