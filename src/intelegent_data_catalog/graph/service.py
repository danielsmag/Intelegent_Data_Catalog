"""Serve graph views from PostgreSQL. Neo4j is a rebuildable copy."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    GraphUnavailableError,
    ValidationAppError,
)
from intelegent_data_catalog.graph.document import GraphDocument, GraphView, traverse
from intelegent_data_catalog.graph.neo4j_store import Neo4jProjection
from intelegent_data_catalog.graph.projection import (
    build_catalog_graph,
    build_lineage_graph,
    merge_documents,
)
from intelegent_data_catalog.lineage.marquez import LineageReader, MarquezError
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

    def _require_depth(self, depth: int) -> None:
        if depth < 1 or depth > 3:
            raise ValidationAppError("depth must be 1, 2, or 3", field="depth")

    def _require_direction(self, direction: str) -> None:
        if direction not in {"upstream", "downstream", "both"}:
            raise ValidationAppError(
                "direction must be upstream, downstream, or both",
                field="direction",
            )
