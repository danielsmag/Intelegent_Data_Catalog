"""Read neighboring tables from the catalog graph projection. No Neo4j writes."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView
from intelegent_data_catalog.knowledge.models import GraphNeighbor, NeighborDirection


class GraphContextProvider(Protocol):
    """Supplies existing table neighbors. Implementations must not write the graph."""

    def get_neighbors(self, table_id: UUID) -> list[GraphNeighbor]:
        """Return catalog tables connected to ``table_id``."""
        ...


class RelationshipGraphReader(Protocol):
    """Read-only relationship view. ``GraphService.relationships`` matches this."""

    def relationships(self, table_id: UUID, depth: int) -> GraphView:
        """Return relationship edges around one table."""
        ...


class GraphPublisher(Protocol):
    """Replaces the Neo4j projection. PostgreSQL stays authoritative."""

    def rebuild(self) -> dict[str, int]:
        """Write the current projection and return node and edge counts."""
        ...


class ProjectionGraphContextProvider:
    """Neighbors from the projection PostgreSQL already serves to Neo4j.

    Disabled catalog relationships are absent here, matching the projection.
    The full relationship list remains on the catalog context.
    """

    def __init__(self, reader: RelationshipGraphReader) -> None:
        self._reader: RelationshipGraphReader = reader

    def get_neighbors(self, table_id: UUID) -> list[GraphNeighbor]:
        """Return direct ``RELATED_TO`` tables. This method does not publish the graph."""
        view: GraphView = self._reader.relationships(table_id, 1)
        start: str = f"table:{table_id}"
        nodes: dict[str, GraphNode] = {node.id: node for node in view.nodes}
        neighbors: list[GraphNeighbor] = []
        seen: set[tuple[UUID, NeighborDirection]] = set()
        edge: GraphEdge
        for edge in view.edges:
            if edge.kind != "RELATED_TO":
                continue
            direction: NeighborDirection | None = _direction(edge, start)
            if direction is None:
                continue
            other_id: str = edge.target if direction == NeighborDirection.OUTGOING else edge.source
            other: GraphNode | None = nodes.get(other_id)
            if other is None or other.kind != "table" or other.catalog_id is None:
                continue
            catalog_id: UUID = _catalog_id(other.catalog_id)
            key: tuple[UUID, NeighborDirection] = (catalog_id, direction)
            if key in seen:
                continue
            seen.add(key)
            neighbors.append(
                GraphNeighbor(
                    table_id=catalog_id,
                    name=other.label,
                    edge_kind="RELATED_TO",
                    direction=direction,
                )
            )
        return neighbors


def _direction(edge: GraphEdge, start: str) -> NeighborDirection | None:
    if edge.source == start:
        return NeighborDirection.OUTGOING
    if edge.target == start:
        return NeighborDirection.INCOMING
    return None


def _catalog_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as err:
        raise ValidationAppError(
            "graph neighbor is missing a catalog id",
            field="table_id",
        ) from err
