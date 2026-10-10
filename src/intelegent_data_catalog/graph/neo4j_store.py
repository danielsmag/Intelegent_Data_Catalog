"""Write the projection to Neo4j. A failed write does not change PostgreSQL."""

from typing import LiteralString, Protocol

from intelegent_data_catalog.exceptions import GraphUnavailableError
from intelegent_data_catalog.graph.document import GraphDocument, GraphEdge, GraphNode, GraphView
from intelegent_data_catalog.graph.knowledge_subgraph import (
    KnowledgeGraphLink,
    KnowledgeGraphNode,
    KnowledgeSubgraphWrite,
    knowledge_graph_view,
    knowledge_link_from_row,
    knowledge_node_from_row,
    knowledge_statements,
)
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
                        _node_merge(node.kind),
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
                        _edge_merge(edge.kind),
                        source=edge.source,
                        target=edge.target,
                    )
        except GraphUnavailableError:
            raise
        except Exception as err:
            raise GraphUnavailableError(sanitize_engine_message(str(err))) from err
        finally:
            driver.close()

    def replace_knowledge(self, document: KnowledgeSubgraphWrite) -> None:
        """Replace one table's column subgraph. Catalog and lineage nodes stay."""
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
                session.execute_write(_apply_knowledge, document)
        except GraphUnavailableError:
            raise
        except Exception as err:
            raise GraphUnavailableError(sanitize_engine_message(str(err))) from err
        finally:
            driver.close()

    def read_knowledge(self, owner_table_id: str) -> GraphView:
        """Return the knowledge subgraph owned by one catalog table."""
        return self.read_owned("owner_table_id", owner_table_id)

    def read_owned(self, owner_property: str, owner_id: str) -> GraphView:
        """Return the knowledge subgraph stored on ``owner_property``."""
        if owner_property == "owner_table_id":
            return self._read_owned(_KNOWLEDGE_NODES, _KNOWLEDGE_EDGES, owner_id)
        if owner_property == "owner_scope_id":
            return self._read_owned(_SCOPE_NODES, _SCOPE_EDGES, owner_id)
        if owner_property == "owner_space_id":
            return self._read_owned(_SPACE_NODES, _SPACE_EDGES, owner_id)
        raise GraphUnavailableError("unknown knowledge owner")

    def _read_owned(
        self,
        nodes_query: LiteralString,
        edges_query: LiteralString,
        owner_id: str,
    ) -> GraphView:
        """Run one pair of knowledge reads."""
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
                nodes: list[KnowledgeGraphNode] = []
                for record in session.run(nodes_query, owner=owner_id):
                    parsed: KnowledgeGraphNode | None = knowledge_node_from_row(
                        record["node_id"],
                        record["catalog_id"],
                        record["name"],
                        record["resolved"],
                        record["labels"],
                    )
                    if parsed is not None:
                        nodes.append(parsed)
                links: list[KnowledgeGraphLink] = []
                for record in session.run(edges_query, owner=owner_id):
                    link: KnowledgeGraphLink | None = knowledge_link_from_row(
                        record["source"],
                        record["target"],
                        record["kind"],
                        record["reason"],
                    )
                    if link is not None:
                        links.append(link)
            return knowledge_graph_view(nodes, links)
        except GraphUnavailableError:
            raise
        except Exception as err:
            raise GraphUnavailableError(sanitize_engine_message(str(err))) from err
        finally:
            driver.close()


_KNOWLEDGE_NODES: LiteralString = (
    "MATCH (n) "
    "WHERE n.projection = 'knowledge' AND n.owner_table_id = $owner "
    "RETURN n.node_id AS node_id, n.catalog_id AS catalog_id, n.name AS name, "
    "n.resolved AS resolved, labels(n) AS labels"
)
_KNOWLEDGE_EDGES: LiteralString = (
    "MATCH (source)-[rel]->(target) "
    "WHERE source.projection = 'knowledge' AND source.owner_table_id = $owner "
    "AND target.projection = 'knowledge' AND target.owner_table_id = $owner "
    "RETURN source.node_id AS source, target.node_id AS target, "
    "type(rel) AS kind, rel.reason AS reason"
)
_SCOPE_NODES: LiteralString = (
    "MATCH (n) "
    "WHERE n.projection = 'knowledge' AND n.owner_scope_id = $owner "
    "RETURN n.node_id AS node_id, n.catalog_id AS catalog_id, n.name AS name, "
    "n.resolved AS resolved, labels(n) AS labels"
)
_SCOPE_EDGES: LiteralString = (
    "MATCH (source)-[rel]->(target) "
    "WHERE source.projection = 'knowledge' AND source.owner_scope_id = $owner "
    "AND target.projection = 'knowledge' AND target.owner_scope_id = $owner "
    "RETURN source.node_id AS source, target.node_id AS target, "
    "type(rel) AS kind, rel.reason AS reason"
)
_SPACE_NODES: LiteralString = (
    "MATCH (n) "
    "WHERE n.projection = 'knowledge' AND n.owner_space_id = $owner "
    "RETURN n.node_id AS node_id, n.catalog_id AS catalog_id, n.name AS name, "
    "n.resolved AS resolved, labels(n) AS labels"
)
_SPACE_EDGES: LiteralString = (
    "MATCH (source)-[rel]->(target) "
    "WHERE source.projection = 'knowledge' AND source.owner_space_id = $owner "
    "AND target.projection = 'knowledge' AND target.owner_space_id = $owner "
    "RETURN source.node_id AS source, target.node_id AS target, "
    "type(rel) AS kind, rel.reason AS reason"
)


class Neo4jColumnSubgraph:
    """Writes the column subgraph for one context build."""

    def __init__(self, projection: Neo4jProjection) -> None:
        self._projection: Neo4jProjection = projection

    def write(self, document: KnowledgeSubgraphWrite) -> None:
        """Replace the knowledge subgraph owned by ``document.owner_table_id``."""
        self._projection.replace_knowledge(document)


class Neo4jSpaceSubgraph:
    """Writes the scope-and-table subgraph for one space context build."""

    def __init__(self, projection: Neo4jProjection) -> None:
        self._projection: Neo4jProjection = projection

    def write(self, document: KnowledgeSubgraphWrite) -> None:
        """Replace the knowledge subgraph owned by the space id on ``document``."""
        self._projection.replace_knowledge(document)


class Neo4jScopeSubgraph:
    """Writes the table subgraph for one scope context build."""

    def __init__(self, projection: Neo4jProjection) -> None:
        self._projection: Neo4jProjection = projection

    def write(self, document: KnowledgeSubgraphWrite) -> None:
        """Replace the knowledge subgraph owned by the scope id on ``document``."""
        self._projection.replace_knowledge(document)


class _CypherTransaction(Protocol):
    """Neo4j transaction surface used to apply one subgraph."""

    def run(
        self,
        query: LiteralString,
        parameters: dict[str, object] | None = None,
        **kwargs: object,
    ) -> object:
        """Execute one statement."""
        ...


def _apply_knowledge(tx: _CypherTransaction, document: KnowledgeSubgraphWrite) -> None:
    """Run the subgraph statements inside one Neo4j transaction."""
    query: LiteralString
    parameters: dict[str, object]
    for query, parameters in knowledge_statements(document):
        tx.run(query, parameters)


_NODE_LABELS: dict[str, LiteralString] = {
    "space": "Space",
    "scope": "Scope",
    "table": "Table",
    "column": "Column",
    "job": "Job",
}
_RELATIONSHIPS: dict[str, LiteralString] = {
    "HAS_SCOPE": "HAS_SCOPE",
    "HAS_TABLE": "HAS_TABLE",
    "HAS_COLUMN": "HAS_COLUMN",
    "REFERENCES": "REFERENCES",
    "RELATED_TO": "RELATED_TO",
    "INPUT_TO": "INPUT_TO",
    "OUTPUTS": "OUTPUTS",
    "JOINS_ON": "JOINS_ON",
    "DERIVED_FROM": "DERIVED_FROM",
    "SAME_MEANING": "SAME_MEANING",
}


def _node_merge(kind: str) -> LiteralString:
    """Cypher that merges one node. The label comes from a fixed set."""
    label: LiteralString | None = _NODE_LABELS.get(kind)
    if label is None:
        raise GraphUnavailableError("unknown graph node kind")
    return (
        "MERGE (n:" + label + " {node_id: $node_id}) "
        "SET n.catalog_id = $catalog_id, n.name = $name, "
        "n.resolved = $resolved, n.projection = $projection"
    )


def _edge_merge(kind: str) -> LiteralString:
    """Cypher that merges one relationship. The type comes from a fixed set."""
    relationship: LiteralString | None = _RELATIONSHIPS.get(kind)
    if relationship is None:
        raise GraphUnavailableError("unknown graph relationship")
    return (
        "MATCH (source {node_id: $source}), (target {node_id: $target}) "
        "MERGE (source)-[:" + relationship + "]->(target)"
    )
