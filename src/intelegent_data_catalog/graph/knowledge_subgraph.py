"""Cypher for one table's column subgraph. Catalog projection nodes are separate."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.exceptions import GraphUnavailableError
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView, edge_style

_EDGE_QUERY: dict[str, str] = {
    "HAS_COLUMN": (
        "MATCH (source {node_id: $source, owner_table_id: $owner}), "
        "(target {node_id: $target, owner_table_id: $owner}) "
        "CREATE (source)-[:HAS_COLUMN {reason: $reason, origin: 'llm'}]->(target)"
    ),
    "REFERENCES": (
        "MATCH (source {node_id: $source, owner_table_id: $owner}), "
        "(target {node_id: $target, owner_table_id: $owner}) "
        "CREATE (source)-[:REFERENCES {reason: $reason, origin: 'llm'}]->(target)"
    ),
    "JOINS_ON": (
        "MATCH (source {node_id: $source, owner_table_id: $owner}), "
        "(target {node_id: $target, owner_table_id: $owner}) "
        "CREATE (source)-[:JOINS_ON {reason: $reason, origin: 'llm'}]->(target)"
    ),
    "DERIVED_FROM": (
        "MATCH (source {node_id: $source, owner_table_id: $owner}), "
        "(target {node_id: $target, owner_table_id: $owner}) "
        "CREATE (source)-[:DERIVED_FROM {reason: $reason, origin: 'llm'}]->(target)"
    ),
    "SAME_MEANING": (
        "MATCH (source {node_id: $source, owner_table_id: $owner}), "
        "(target {node_id: $target, owner_table_id: $owner}) "
        "CREATE (source)-[:SAME_MEANING {reason: $reason, origin: 'llm'}]->(target)"
    ),
}


class KnowledgeNodeWrite(BaseModel):
    """One table or column node in a knowledge subgraph."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    catalog_id: str
    name: str


class KnowledgeEdgeWrite(BaseModel):
    """One directed edge in a knowledge subgraph."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    kind: str
    reason: str | None = None


class KnowledgeSubgraphWrite(BaseModel):
    """Replacement payload for the subgraph owned by one catalog table."""

    model_config = ConfigDict(extra="forbid")

    owner_table_id: str
    tables: list[KnowledgeNodeWrite]
    columns: list[KnowledgeNodeWrite]
    edges: list[KnowledgeEdgeWrite]


def knowledge_statements(
    document: KnowledgeSubgraphWrite,
) -> list[tuple[str, dict[str, object]]]:
    """Return the Cypher that deletes and recreates one knowledge subgraph."""
    owner: str = document.owner_table_id
    statements: list[tuple[str, dict[str, object]]] = [
        (
            "MATCH (n) WHERE n.projection = 'knowledge' AND n.owner_table_id = $owner "
            "DETACH DELETE n",
            {"owner": owner},
        ),
        (
            "UNWIND $tables AS node "
            "CREATE (n:Table {node_id: node.node_id, catalog_id: node.catalog_id, "
            "name: node.name, resolved: true, projection: 'knowledge', "
            "owner_table_id: $owner})",
            {"owner": owner, "tables": [_node_row(node) for node in document.tables]},
        ),
        (
            "UNWIND $columns AS node "
            "CREATE (n:Column {node_id: node.node_id, catalog_id: node.catalog_id, "
            "name: node.name, resolved: true, projection: 'knowledge', "
            "owner_table_id: $owner})",
            {"owner": owner, "columns": [_node_row(node) for node in document.columns]},
        ),
    ]
    edge: KnowledgeEdgeWrite
    for edge in document.edges:
        query: str | None = _EDGE_QUERY.get(edge.kind)
        if query is None:
            raise GraphUnavailableError("unknown graph relationship")
        statements.append(
            (
                query,
                {
                    "owner": owner,
                    "source": edge.source,
                    "target": edge.target,
                    "reason": edge.reason,
                },
            )
        )
    return statements


def _node_row(node: KnowledgeNodeWrite) -> dict[str, str]:
    return {"node_id": node.node_id, "catalog_id": node.catalog_id, "name": node.name}


class KnowledgeGraphNode(BaseModel):
    """One Table or Column node read back from a knowledge subgraph."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    catalog_id: str | None
    name: str
    kind: Literal["table", "column"]
    resolved: bool = True


class KnowledgeGraphLink(BaseModel):
    """One directed relationship read back from a knowledge subgraph."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    kind: str
    reason: str | None = None


def knowledge_node_from_row(
    node_id: object,
    catalog_id: object,
    name: object,
    resolved: object,
    labels: object,
) -> KnowledgeGraphNode | None:
    """Map one Neo4j row onto a subgraph node. Unknown labels are skipped."""
    identity: str | None = _text(node_id)
    kind: Literal["table", "column"] | None = _kind(labels)
    if identity is None or kind is None:
        return None
    label: str | None = _text(name)
    return KnowledgeGraphNode(
        node_id=identity,
        catalog_id=_text(catalog_id),
        name=label if label is not None else identity,
        kind=kind,
        resolved=resolved if isinstance(resolved, bool) else True,
    )


def knowledge_link_from_row(
    source: object,
    target: object,
    kind: object,
    reason: object,
) -> KnowledgeGraphLink | None:
    """Map one Neo4j relationship row. A row without endpoints is skipped."""
    source_id: str | None = _text(source)
    target_id: str | None = _text(target)
    relation: str | None = _text(kind)
    if source_id is None or target_id is None or relation is None:
        return None
    return KnowledgeGraphLink(
        source=source_id,
        target=target_id,
        kind=relation,
        reason=_text(reason),
    )


def knowledge_graph_view(
    nodes: list[KnowledgeGraphNode],
    links: list[KnowledgeGraphLink],
) -> GraphView:
    """Turn a knowledge subgraph into the bounded graph the API returns."""
    seen: set[str] = set()
    graph_nodes: list[GraphNode] = []
    node: KnowledgeGraphNode
    for node in nodes:
        if node.node_id in seen:
            continue
        seen.add(node.node_id)
        graph_nodes.append(
            GraphNode(
                id=node.node_id,
                kind=node.kind,
                label=node.name,
                catalog_id=node.catalog_id,
                resolved=node.resolved,
            )
        )
    graph_nodes.sort(key=lambda item: (0 if item.kind == "table" else 1, item.label))
    seen_edges: set[tuple[str, str, str]] = set()
    edges: list[GraphEdge] = []
    link: KnowledgeGraphLink
    for link in links:
        key: tuple[str, str, str] = (link.source, link.target, link.kind)
        if link.source not in seen or link.target not in seen or key in seen_edges:
            continue
        seen_edges.add(key)
        edges.append(
            GraphEdge(
                source=link.source,
                target=link.target,
                kind=link.kind,
                style=edge_style(link.kind),
                reason=link.reason,
            )
        )
    return GraphView(nodes=graph_nodes, edges=edges, truncated=False)


def _kind(labels: object) -> Literal["table", "column"] | None:
    if isinstance(labels, str) or not isinstance(labels, (list, tuple)):
        return None
    names: list[str] = [item for item in labels if isinstance(item, str)]
    if "Table" in names:
        return "table"
    if "Column" in names:
        return "column"
    return None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text
