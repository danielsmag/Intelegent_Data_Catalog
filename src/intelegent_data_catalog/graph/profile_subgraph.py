"""Cypher for one asset's data-profile subgraph. Catalog projection nodes stay separate."""

from typing import Literal, LiteralString

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from intelegent_data_catalog.exceptions import GraphUnavailableError
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView, edge_style

_PROFILE_LABELS: dict[str, LiteralString] = {
    "catalog": "Catalog",
    "schema": "Schema",
    "table": "Table",
    "column": "Column",
}
_PROFILE_EDGES: dict[str, LiteralString] = {
    "HAS_SCHEMA": "HAS_SCHEMA",
    "HAS_TABLE": "HAS_TABLE",
    "HAS_COLUMN": "HAS_COLUMN",
    "CANDIDATE_REFERENCE": "CANDIDATE_REFERENCE",
    "SAME_NAME": "SAME_NAME",
}
_SKIPPED_PROPERTIES: frozenset[str] = frozenset(
    {"node_id", "name", "projection", "owner_asset_id", "resolved", "catalog_id"},
)


class ProfileNodeWrite(BaseModel):
    """One catalog, schema, table, or column node in a profile subgraph."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    name: str
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class ProfileEdgeWrite(BaseModel):
    """One directed edge in a profile subgraph."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    kind: str
    reason: str | None = None
    score: float | None = None


class ProfileSubgraphWrite(BaseModel):
    """Replacement payload for the profile subgraph owned by one asset."""

    model_config = ConfigDict(extra="forbid")

    owner_asset_id: str
    catalogs: list[ProfileNodeWrite] = Field(default_factory=list)
    schemas: list[ProfileNodeWrite] = Field(default_factory=list)
    tables: list[ProfileNodeWrite] = Field(default_factory=list)
    columns: list[ProfileNodeWrite] = Field(default_factory=list)
    edges: list[ProfileEdgeWrite] = Field(default_factory=list)


class ProfileGraphNode(BaseModel):
    """One node read back from a profile subgraph."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    name: str
    kind: Literal["scope", "table", "column"]
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class ProfileGraphLink(BaseModel):
    """One relationship read back from a profile subgraph."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    kind: str
    reason: str | None = None


def profile_statements(
    document: ProfileSubgraphWrite,
) -> list[tuple[LiteralString, dict[str, object]]]:
    """Return the Cypher that deletes and recreates one profile subgraph."""
    owner: str = document.owner_asset_id
    statements: list[tuple[LiteralString, dict[str, object]]] = [
        (
            "MATCH (n) WHERE n.projection = 'profile' AND n.owner_asset_id = $owner "
            "DETACH DELETE n",
            {"owner": owner},
        ),
        (
            _create_nodes("catalog"),
            {"owner": owner, "nodes": [_node_row(node) for node in document.catalogs]},
        ),
        (
            _create_nodes("schema"),
            {"owner": owner, "nodes": [_node_row(node) for node in document.schemas]},
        ),
        (
            _create_nodes("table"),
            {"owner": owner, "nodes": [_node_row(node) for node in document.tables]},
        ),
        (
            _create_nodes("column"),
            {"owner": owner, "nodes": [_node_row(node) for node in document.columns]},
        ),
    ]
    edge: ProfileEdgeWrite
    for edge in document.edges:
        statements.append(
            (
                _edge_query(edge.kind),
                {
                    "owner": owner,
                    "source": edge.source,
                    "target": edge.target,
                    "reason": edge.reason,
                    "score": edge.score,
                },
            ),
        )
    return statements


def profile_node_from_row(
    node_id: object,
    name: object,
    labels: object,
    props: object,
) -> ProfileGraphNode | None:
    """Map one Neo4j row onto a profile node. Unknown labels are skipped."""
    identity: str | None = _text(node_id)
    kind: Literal["scope", "table", "column"] | None = _kind(labels)
    if identity is None or kind is None:
        return None
    label: str | None = _text(name)
    return ProfileGraphNode(
        node_id=identity,
        name=label if label is not None else identity,
        kind=kind,
        properties=_properties(props),
    )


def profile_link_from_row(
    source: object,
    target: object,
    kind: object,
    reason: object,
) -> ProfileGraphLink | None:
    """Map one Neo4j relationship row. A row without endpoints is skipped."""
    source_id: str | None = _text(source)
    target_id: str | None = _text(target)
    relation: str | None = _text(kind)
    if source_id is None or target_id is None or relation is None:
        return None
    return ProfileGraphLink(
        source=source_id,
        target=target_id,
        kind=relation,
        reason=_text(reason),
    )


def profile_graph_view(nodes: list[ProfileGraphNode], links: list[ProfileGraphLink]) -> GraphView:
    """Turn a profile subgraph into the bounded graph the API returns."""
    seen: set[str] = set()
    graph_nodes: list[GraphNode] = []
    node: ProfileGraphNode
    for node in nodes:
        if node.node_id in seen:
            continue
        seen.add(node.node_id)
        graph_nodes.append(
            GraphNode(
                id=node.node_id,
                kind=node.kind,
                label=node.name,
                resolved=True,
                properties=dict(node.properties),
            ),
        )
    graph_nodes.sort(key=lambda item: (_kind_order(item.kind), item.label, item.id))
    seen_edges: set[tuple[str, str, str]] = set()
    edges: list[GraphEdge] = []
    link: ProfileGraphLink
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
            ),
        )
    return GraphView(nodes=graph_nodes, edges=edges, truncated=False)


def _create_nodes(kind: str) -> LiteralString:
    label: LiteralString | None = _PROFILE_LABELS.get(kind)
    if label is None:
        raise GraphUnavailableError("unknown profile node kind")
    return (
        "UNWIND $nodes AS node "
        "CREATE (n:" + label + " {node_id: node.node_id, name: node.name, "
        "resolved: true, projection: 'profile', owner_asset_id: $owner}) "
        "SET n += node.props"
    )


def _edge_query(kind: str) -> LiteralString:
    relationship: LiteralString | None = _PROFILE_EDGES.get(kind)
    if relationship is None:
        raise GraphUnavailableError("unknown graph relationship")
    return (
        "MATCH (source {node_id: $source, owner_asset_id: $owner, projection: 'profile'}), "
        "(target {node_id: $target, owner_asset_id: $owner, projection: 'profile'}) "
        "CREATE (source)-[:"
        + relationship
        + " {reason: $reason, score: $score, origin: 'profile'}]->(target)"
    )


def _node_row(node: ProfileNodeWrite) -> dict[str, object]:
    return {"node_id": node.node_id, "name": node.name, "props": dict(node.properties)}


def _kind(labels: object) -> Literal["scope", "table", "column"] | None:
    if isinstance(labels, str) or not isinstance(labels, (list, tuple)):
        return None
    names: list[str] = [item for item in labels if isinstance(item, str)]
    if "Column" in names:
        return "column"
    if "Table" in names:
        return "table"
    if "Catalog" in names or "Schema" in names:
        return "scope"
    return None


def _kind_order(kind: str) -> int:
    match kind:
        case "scope":
            return 0
        case "table":
            return 1
        case _:
            return 2


def _properties(raw: object) -> dict[str, JsonValue]:
    if not isinstance(raw, dict):
        return {}
    found: dict[str, JsonValue] = {}
    key: object
    value: object
    for key, value in raw.items():
        name: str = str(key)
        if name in _SKIPPED_PROPERTIES:
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            found[name] = value
            continue
        found[name] = str(value)
    return found


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text
