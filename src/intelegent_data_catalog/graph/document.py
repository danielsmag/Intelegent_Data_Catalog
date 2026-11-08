"""In-memory graph document and bounded traversal. Neo4j stores the same shape."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class GraphNode(BaseModel):
    """One node in a catalog or lineage projection."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    label: str
    catalog_id: str | None = None
    resolved: bool = True
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """Directed projection edge."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    kind: str
    style: str
    reason: str | None = None


class GraphDocument(BaseModel):
    """Full projection used by rebuild and by traversal."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class GraphView(BaseModel):
    """Bounded traversal result for the API."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool


_HIERARCHY: frozenset[str] = frozenset(
    {"HAS_SCOPE", "HAS_CATALOG", "HAS_SCHEMA", "HAS_TABLE", "HAS_COLUMN"},
)
_RELATIONSHIP: frozenset[str] = frozenset(
    {
        "REFERENCES",
        "RELATED_TO",
        "JOINS_ON",
        "DERIVED_FROM",
        "SAME_MEANING",
        "CANDIDATE_REFERENCE",
        "SAME_NAME",
    },
)
_LINEAGE: frozenset[str] = frozenset({"INPUT_TO", "OUTPUTS", "READS", "WRITES", "MENTIONS"})


def edge_style(kind: str) -> str:
    """Map a stored relationship type to the style the UI draws."""
    if kind in _HIERARCHY:
        return "hierarchy"
    if kind in _RELATIONSHIP:
        return "relationship"
    if kind in _LINEAGE:
        return "lineage"
    return "hierarchy"


def traverse(
    document: GraphDocument,
    start_id: str,
    *,
    kinds: set[str],
    depth: int,
    node_limit: int,
    direction: str,
) -> GraphView:
    """Walk out from one node. Depth counts edges. Lineage uses two edges per dataset hop."""
    nodes_by_id: dict[str, GraphNode] = {node.id: node for node in document.nodes}
    if start_id not in nodes_by_id:
        return GraphView(nodes=[], edges=[], truncated=False)
    hop_limit: int = depth * 2 if kinds & set(_LINEAGE) else depth
    outgoing: dict[str, list[GraphEdge]] = {}
    incoming: dict[str, list[GraphEdge]] = {}
    edge: GraphEdge
    for edge in document.edges:
        if edge.kind not in kinds:
            continue
        outgoing.setdefault(edge.source, []).append(edge)
        incoming.setdefault(edge.target, []).append(edge)
    seen: set[str] = {start_id}
    kept_edges: list[GraphEdge] = []
    frontier: list[tuple[str, int]] = [(start_id, 0)]
    truncated: bool = False
    while frontier:
        current: str
        distance: int
        current, distance = frontier.pop(0)
        if distance >= hop_limit:
            continue
        neighbors: list[GraphEdge] = []
        if direction in {"downstream", "both"}:
            neighbors.extend(outgoing.get(current, []))
        if direction in {"upstream", "both"}:
            neighbors.extend(incoming.get(current, []))
        step: GraphEdge
        for step in neighbors:
            other: str = step.target if step.source == current else step.source
            if other not in nodes_by_id:
                continue
            if step not in kept_edges:
                kept_edges.append(step)
            if other in seen:
                continue
            if len(seen) >= node_limit:
                truncated = True
                continue
            seen.add(other)
            frontier.append((other, distance + 1))
    nodes: list[GraphNode] = [nodes_by_id[node_id] for node_id in seen]
    return GraphView(nodes=nodes, edges=kept_edges, truncated=truncated)
