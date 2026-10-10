"""Serve graph views from PostgreSQL. Neo4j is a rebuildable copy."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    GraphUnavailableError,
    ValidationAppError,
)
from intelegent_data_catalog.graph.document import (
    GraphDocument,
    GraphEdge,
    GraphNode,
    GraphView,
    edge_style,
    traverse,
)
from intelegent_data_catalog.graph.neo4j_store import Neo4jProjection
from intelegent_data_catalog.graph.projection import (
    build_catalog_graph,
    build_lineage_graph,
    merge_documents,
)
from intelegent_data_catalog.lineage.marquez import LineageReader, MarquezError
from intelegent_data_catalog.persistence.enums import RelationshipStatus, RelationshipType
from intelegent_data_catalog.persistence.models import CatalogTableRow, RelationshipRow, ScopeRow
from intelegent_data_catalog.registry.repository import RegistryRepository

_NODE_LIMIT: int = 200
_RELATIONSHIP_KINDS: set[str] = {"RELATED_TO", "REFERENCES"}
_LINEAGE_KINDS: set[str] = {"INPUT_TO", "OUTPUTS"}
_CONTEXT_KINDS: set[str] = (
    _RELATIONSHIP_KINDS
    | _LINEAGE_KINDS
    | {
        "HAS_SCOPE",
        "HAS_TABLE",
        "HAS_COLUMN",
    }
)


class GraphService:
    """Build bounded graphs. A Neo4j outage does not change catalog rows."""

    def __init__(
        self,
        session: Session,
        lineage: LineageReader | None = None,
        neo4j: Neo4jProjection | None = None,
    ) -> None:
        self._repo: RegistryRepository = RegistryRepository(session)
        self._lineage: LineageReader | None = lineage
        self._neo4j: Neo4jProjection | None = neo4j

    def relationships(self, table_id: UUID, depth: int) -> GraphView:
        """Return catalog relationship edges around one table."""
        self._require_depth(depth)
        self._require_table(table_id)
        return traverse(
            self._catalog_document(),
            f"table:{table_id}",
            kinds=_RELATIONSHIP_KINDS,
            depth=depth,
            node_limit=_NODE_LIMIT,
            direction="both",
        )

    def lineage(self, table_id: UUID, depth: int, direction: str) -> GraphView:
        """Return operational lineage around one table."""
        self._require_depth(depth)
        self._require_direction(direction)
        self._require_table(table_id)
        return traverse(
            self._full_document(),
            f"table:{table_id}",
            kinds=_LINEAGE_KINDS,
            depth=depth,
            node_limit=_NODE_LIMIT,
            direction=direction,
        )

    def context(self, table_id: UUID, depth: int) -> GraphView:
        """Return hierarchy, relationships, and lineage around one table."""
        self._require_depth(depth)
        self._require_table(table_id)
        return traverse(
            self._full_document(),
            f"table:{table_id}",
            kinds=_CONTEXT_KINDS,
            depth=depth,
            node_limit=_NODE_LIMIT,
            direction="both",
        )

    def knowledge_subgraph(self, table_id: UUID) -> GraphView:
        """Return the Neo4j column subgraph owned by one table."""
        self._require_table(table_id)
        if self._neo4j is None:
            raise GraphUnavailableError("graph projection is not configured")
        return self._neo4j.read_knowledge(str(table_id))

    def scope_overview(self, scope_id: UUID) -> GraphView:
        """Return tables in one scope and the catalog relationships between them."""
        self._require_scope(scope_id)
        tables: list[CatalogTableRow] = self._repo.list_tables(scope_id)
        table_ids: set[UUID] = {table.id for table in tables}
        nodes: list[GraphNode] = []
        table: CatalogTableRow
        for table in tables:
            label: str = table.display_name if table.display_name else table.name
            nodes.append(
                GraphNode(
                    id=f"table:{table.id}",
                    kind="table",
                    label=label,
                    catalog_id=str(table.id),
                    resolved=True,
                )
            )
        edges: list[GraphEdge] = []
        relation: RelationshipRow
        for relation in self._repo.list_relationships_between(table_ids):
            if relation.status == RelationshipStatus.DISABLED.value:
                continue
            kind: str = (
                "REFERENCES"
                if relation.relationship_type == RelationshipType.FOREIGN_KEY.value
                else "RELATED_TO"
            )
            edges.append(
                GraphEdge(
                    source=f"table:{relation.source_table_id}",
                    target=f"table:{relation.target_table_id}",
                    kind=kind,
                    style=edge_style(kind),
                    reason=_relationship_reason(relation),
                )
            )
        return GraphView(nodes=nodes, edges=edges, truncated=False)

    def space_overview(self, space_id: UUID) -> GraphView:
        """Return scopes, their tables, and the catalog relationships in one space."""
        self._require_space(space_id)
        scopes: list[ScopeRow] = self._repo.list_scopes(space_id)
        nodes: list[GraphNode] = []
        scope: ScopeRow
        for scope in scopes:
            nodes.append(
                GraphNode(
                    id=f"scope:{scope.id}",
                    kind="scope",
                    label=scope.name,
                    catalog_id=str(scope.id),
                    resolved=True,
                )
            )
        tables: list[CatalogTableRow] = []
        truncated: bool = False
        for scope in scopes:
            row: CatalogTableRow
            for row in self._repo.list_tables(scope.id):
                if len(tables) >= _NODE_LIMIT:
                    truncated = True
                    break
                tables.append(row)
            if truncated:
                break
        table_ids: set[UUID] = {table.id for table in tables}
        table: CatalogTableRow
        for table in tables:
            label: str = table.display_name if table.display_name else table.name
            nodes.append(
                GraphNode(
                    id=f"table:{table.id}",
                    kind="table",
                    label=label,
                    catalog_id=str(table.id),
                    resolved=True,
                )
            )
        edges: list[GraphEdge] = []
        for table in tables:
            edges.append(
                GraphEdge(
                    source=f"scope:{table.scope_id}",
                    target=f"table:{table.id}",
                    kind="HAS_TABLE",
                    style=edge_style("HAS_TABLE"),
                    reason=None,
                )
            )
        relation: RelationshipRow
        for relation in self._repo.list_relationships_between(table_ids):
            if relation.status == RelationshipStatus.DISABLED.value:
                continue
            kind: str = (
                "REFERENCES"
                if relation.relationship_type == RelationshipType.FOREIGN_KEY.value
                else "RELATED_TO"
            )
            edges.append(
                GraphEdge(
                    source=f"table:{relation.source_table_id}",
                    target=f"table:{relation.target_table_id}",
                    kind=kind,
                    style=edge_style(kind),
                    reason=_relationship_reason(relation),
                )
            )
        return GraphView(nodes=nodes, edges=edges, truncated=truncated)

    def scope_knowledge_subgraph(self, scope_id: UUID) -> GraphView:
        """Return the Neo4j table subgraph owned by one scope."""
        self._require_scope(scope_id)
        if self._neo4j is None:
            raise GraphUnavailableError("graph projection is not configured")
        return self._neo4j.read_owned("owner_scope_id", str(scope_id))

    def space_knowledge_subgraph(self, space_id: UUID) -> GraphView:
        """Return the Neo4j subgraph owned by one space."""
        self._require_space(space_id)
        if self._neo4j is None:
            raise GraphUnavailableError("graph projection is not configured")
        return self._neo4j.read_owned("owner_space_id", str(space_id))

    def publish(self) -> None:
        """Write the current projection when Neo4j is configured."""
        if self._neo4j is None:
            return
        self._neo4j.write(self._full_document())

    def rebuild(self) -> dict[str, int]:
        """Replace the Neo4j projection from PostgreSQL and Marquez."""
        document: GraphDocument = self._full_document()
        if self._neo4j is None:
            raise GraphUnavailableError("graph projection is not configured")
        self._neo4j.write(document)
        return {
            "nodes": len(document.nodes),
            "edges": len(document.edges),
        }

    def _catalog_document(self) -> GraphDocument:
        return build_catalog_graph(
            self._repo.list_all_spaces(),
            self._repo.list_all_scopes(),
            self._repo.list_all_tables(),
            self._repo.list_all_columns(),
            self._repo.list_all_relationships(),
            self._repo.list_all_mappings(),
        )

    def _full_document(self) -> GraphDocument:
        catalog: GraphDocument = self._catalog_document()
        if self._lineage is None:
            return catalog
        try:
            lineage: GraphDocument = build_lineage_graph(
                self._lineage.read_links(),
                self._repo.list_marquez_mappings(),
            )
        except MarquezError:
            return catalog
        return merge_documents(catalog, lineage)

    def _require_table(self, table_id: UUID) -> None:
        if self._repo.get_table(table_id) is None:
            raise EntityNotFoundError("table", str(table_id))

    def _require_scope(self, scope_id: UUID) -> None:
        if self._repo.get_scope(scope_id) is None:
            raise EntityNotFoundError("scope", str(scope_id))

    def _require_space(self, space_id: UUID) -> None:
        if self._repo.get_space(space_id) is None:
            raise EntityNotFoundError("space", str(space_id))

    def _require_depth(self, depth: int) -> None:
        if depth < 1 or depth > 3:
            raise ValidationAppError("depth must be 1, 2, or 3", field="depth")

    def _require_direction(self, direction: str) -> None:
        if direction not in {"upstream", "downstream", "both"}:
            raise ValidationAppError(
                "direction must be upstream, downstream, or both",
                field="direction",
            )


def _relationship_reason(relation: RelationshipRow) -> str:
    description: str = relation.description.strip() if relation.description else ""
    if description == "":
        return relation.origin
    return f"{relation.origin}: {description}"
