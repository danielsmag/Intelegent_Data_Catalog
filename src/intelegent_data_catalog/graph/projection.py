"""Build the Neo4j projection from PostgreSQL and Marquez. Postgres wins on rebuild."""

from uuid import UUID

from intelegent_data_catalog.graph.document import GraphDocument, GraphEdge, GraphNode, edge_style
from intelegent_data_catalog.persistence.enums import RelationshipStatus
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    ColumnRow,
    MarquezMappingRow,
    RelationshipColumnMappingRow,
    RelationshipRow,
    ScopeRow,
    SpaceRow,
)


class LineageLink:
    """One job input or output. Unmapped datasets stay unresolved."""

    def __init__(
        self,
        input_namespace: str,
        input_name: str,
        job_namespace: str,
        job_name: str,
        output_namespace: str,
        output_name: str,
    ) -> None:
        self.input_namespace: str = input_namespace
        self.input_name: str = input_name
        self.job_namespace: str = job_namespace
        self.job_name: str = job_name
        self.output_namespace: str = output_namespace
        self.output_name: str = output_name


def build_catalog_graph(
    spaces: list[SpaceRow],
    scopes: list[ScopeRow],
    tables: list[CatalogTableRow],
    columns: list[ColumnRow],
    relationships: list[RelationshipRow],
    mappings: list[RelationshipColumnMappingRow],
) -> GraphDocument:
    """Project catalog identity. Node ids are the PostgreSQL UUIDs, not names."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    space: SpaceRow
    for space in spaces:
        nodes.append(_node("space", space.id, "space", space.name))
    scope: ScopeRow
    for scope in scopes:
        nodes.append(_node("scope", scope.id, "scope", scope.name))
        edges.append(_edge(f"space:{scope.space_id}", f"scope:{scope.id}", "HAS_SCOPE"))
    table: CatalogTableRow
    for table in tables:
        nodes.append(_node("table", table.id, "table", table.name))
        edges.append(_edge(f"scope:{table.scope_id}", f"table:{table.id}", "HAS_TABLE"))
    column: ColumnRow
    for column in columns:
        nodes.append(_node("column", column.id, "column", column.name))
        edges.append(_edge(f"table:{column.table_id}", f"column:{column.id}", "HAS_COLUMN"))
    active: dict[UUID, RelationshipRow] = {
        item.id: item for item in relationships if item.status != RelationshipStatus.DISABLED.value
    }
    mapping: RelationshipColumnMappingRow
    for mapping in mappings:
        relationship: RelationshipRow | None = active.get(mapping.relationship_id)
        if relationship is None:
            continue
        edges.append(
            _edge(
                f"column:{mapping.source_column_id}",
                f"column:{mapping.target_column_id}",
                "REFERENCES",
            )
        )
        related: GraphEdge = _edge(
            f"table:{relationship.source_table_id}",
            f"table:{relationship.target_table_id}",
            "RELATED_TO",
        )
        if related not in edges:
            edges.append(related)
    return GraphDocument(nodes=nodes, edges=edges)


def build_lineage_graph(
    links: list[LineageLink],
    mappings: list[MarquezMappingRow],
) -> GraphDocument:
    """Project jobs and datasets. Unresolved datasets keep a lineage id and no catalog id."""
    by_dataset: dict[tuple[str, str], UUID] = {
        (item.marquez_namespace, item.marquez_dataset_name): item.catalog_table_id
        for item in mappings
    }
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    link: LineageLink
    for link in links:
        job_id: str = f"job:{link.job_namespace}:{link.job_name}"
        nodes[job_id] = GraphNode(id=job_id, kind="job", label=link.job_name, catalog_id=None)
        source_id: str = _dataset_node(nodes, by_dataset, link.input_namespace, link.input_name)
        target_id: str = _dataset_node(nodes, by_dataset, link.output_namespace, link.output_name)
        edges.append(_edge(source_id, job_id, "INPUT_TO"))
        edges.append(_edge(job_id, target_id, "OUTPUTS"))
    return GraphDocument(nodes=list(nodes.values()), edges=edges)


def merge_documents(left: GraphDocument, right: GraphDocument) -> GraphDocument:
    """Combine catalog and lineage projections. Matching table ids stay one node."""
    nodes: dict[str, GraphNode] = {node.id: node for node in left.nodes}
    node: GraphNode
    for node in right.nodes:
        current: GraphNode | None = nodes.get(node.id)
        if current is None:
            nodes[node.id] = node
            continue
        if current.catalog_id is None and node.catalog_id is not None:
            nodes[node.id] = node
    edges: list[GraphEdge] = []
    edge: GraphEdge
    for edge in [*left.edges, *right.edges]:
        if edge not in edges:
            edges.append(edge)
    return GraphDocument(nodes=list(nodes.values()), edges=edges)


def _dataset_node(
    nodes: dict[str, GraphNode],
    by_dataset: dict[tuple[str, str], UUID],
    namespace: str,
    name: str,
) -> str:
    catalog_id: UUID | None = by_dataset.get((namespace, name))
    if catalog_id is not None:
        node_id: str = f"table:{catalog_id}"
        if node_id not in nodes:
            nodes[node_id] = GraphNode(
                id=node_id,
                kind="table",
                label=name,
                catalog_id=str(catalog_id),
                resolved=True,
            )
        return node_id
    unresolved_id: str = f"dataset:{namespace}:{name}"
    nodes[unresolved_id] = GraphNode(
        id=unresolved_id,
        kind="table",
        label=name,
        catalog_id=None,
        resolved=False,
    )
    return unresolved_id


def _node(prefix: str, entity_id: UUID, kind: str, label: str) -> GraphNode:
    return GraphNode(id=f"{prefix}:{entity_id}", kind=kind, label=label, catalog_id=str(entity_id))


def _edge(source: str, target: str, kind: str) -> GraphEdge:
    return GraphEdge(source=source, target=target, kind=kind, style=edge_style(kind))
