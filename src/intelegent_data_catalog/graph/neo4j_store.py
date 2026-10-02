"""Write the projection to Neo4j. A failed write does not change PostgreSQL."""

from intelegent_data_catalog.exceptions import GraphUnavailableError
from intelegent_data_catalog.graph.document import GraphDocument, GraphEdge, GraphNode
from intelegent_data_catalog.sources.trino.client import sanitize_engine_message


class Neo4jProjection:
    """Replace catalog and lineage nodes. Identity is catalog_id, not the table name."""

    def __init__(self, uri: str, user: str, password: str, database: str) -> None:
        self._uri: str = uri
        self._user: str = user
        self._password: str = password
        self._database: str = database

    def write(self, document: GraphDocument) -> None:
        """Clear the previous projection and write this one."""
        try:
            from neo4j import GraphDatabase
        except ImportError as err:
            raise GraphUnavailableError("Neo4j driver is not installed") from err
        try:
            driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
        except Exception as err:
            raise GraphUnavailableError(sanitize_engine_message(str(err))) from err
        try:
            with driver.session(database=self._database) as session:
                session.run(
                    "MATCH (n) WHERE n.projection IN ['catalog', 'lineage'] DETACH DELETE n"
                )
                node: GraphNode
                for node in document.nodes:
                    session.run(
                        f"MERGE (n:{_label(node.kind)} {{node_id: $node_id}}) "
                        "SET n.catalog_id = $catalog_id, n.name = $name, "
                        "n.resolved = $resolved, n.projection = $projection",
                        node_id=node.id,
                        catalog_id=node.catalog_id,
                        name=node.label,
                        resolved=node.resolved,
                        projection="lineage"
                        if node.kind == "job" or not node.resolved
                        else "catalog",
                    )
                edge: GraphEdge
                for edge in document.edges:
                    session.run(
                        "MATCH (source {node_id: $source}), (target {node_id: $target}) "
                        f"MERGE (source)-[:{_relationship(edge.kind)}]->(target)",
                        source=edge.source,
                        target=edge.target,
                    )
        except GraphUnavailableError:
            raise
        except Exception as err:
            raise GraphUnavailableError(sanitize_engine_message(str(err))) from err
        finally:
            driver.close()


def _label(kind: str) -> str:
    labels: dict[str, str] = {
        "space": "Space",
        "scope": "Scope",
        "table": "Table",
        "column": "Column",
        "job": "Job",
    }
    label: str | None = labels.get(kind)
    if label is None:
        raise GraphUnavailableError("unknown graph node kind")
    return label


def _relationship(kind: str) -> str:
    allowed: frozenset[str] = frozenset(
        {"HAS_SCOPE", "HAS_TABLE", "HAS_COLUMN", "REFERENCES", "RELATED_TO", "INPUT_TO", "OUTPUTS"}
    )
    if kind not in allowed:
        raise GraphUnavailableError("unknown graph relationship")
    return kind
