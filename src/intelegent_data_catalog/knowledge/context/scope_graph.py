"""Turn a scope catalog into cross-table links and a scope-owned subgraph."""

import logging
from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.graph.knowledge_subgraph import (
    KnowledgeEdgeWrite,
    KnowledgeNodeWrite,
    KnowledgeSubgraphWrite,
)
from intelegent_data_catalog.knowledge.context.column_graph import (
    GraphBuilder,
    KnowledgeSubgraphWriter,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnMappingSummary,
    ColumnMetadata,
    ScopeAnalysisContext,
    ScopeCatalogContext,
    ScopeProposal,
    ScopeTableSummary,
    TableLink,
    TableRelation,
)
from intelegent_data_catalog.persistence.enums import RelationshipOrigin

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")

_MAX_LINKS: int = 60


class ScopeRelationProposer(Protocol):
    """Selects cross-table links and a summary. Implementations may call a model."""

    def propose(self, context: ScopeCatalogContext) -> ScopeProposal:
        """Return links whose tables and columns come from ``context``."""
        ...


class ScopeRelationshipStore(Protocol):
    """Replaces LLM relationships inside one scope. Other origins stay."""

    def replace(self, scope_table_ids: set[UUID], links: list[TableLink]) -> None:
        """Delete prior LLM rows for these tables and insert ``links``."""
        ...


class ScopeProjectionPublisher(Protocol):
    """Republishes the catalog projection after PostgreSQL relationships change."""

    def publish(self) -> None:
        """Write the current projection. A missing graph store does nothing."""
        ...


class EmptyKnowledgeSubgraphWriter:
    """Drops the scope subgraph write when Neo4j is not configured."""

    def write(self, document: KnowledgeSubgraphWrite) -> None:
        """Ignore ``document``. PostgreSQL remains the stored result."""
        del document


class ScopeGraphAppender(GraphBuilder[ScopeCatalogContext, ScopeAnalysisContext], Protocol):
    """Accepts scope links, stores them, and writes the scope subgraph."""

    def apply(
        self,
        context: ScopeCatalogContext,
        *,
        include_summary: bool = True,
    ) -> ScopeAnalysisContext:
        """Propose links, store them, and return the scope context."""
        ...


class ScopeGraphBuilder(ScopeGraphAppender):
    """Accept model links, store them, and write the scope subgraph."""

    def __init__(
        self,
        proposer: ScopeRelationProposer,
        writer: KnowledgeSubgraphWriter,
        relationships: ScopeRelationshipStore,
        publisher: ScopeProjectionPublisher | None = None,
    ) -> None:
        self._proposer: ScopeRelationProposer = proposer
        self._writer: KnowledgeSubgraphWriter = writer
        self._relationships: ScopeRelationshipStore = relationships
        self._publisher: ScopeProjectionPublisher | None = publisher

    def apply(
        self,
        context: ScopeCatalogContext,
        *,
        include_summary: bool = True,
    ) -> ScopeAnalysisContext:
        """Propose links, replace LLM relationships, and return the scope context."""
        proposed: ScopeProposal = self._proposer.propose(context)
        accepted: list[TableLink] = accept_table_links(context, proposed.links)
        link: TableLink
        for link in accepted:
            logger.info(
                "scope link scope_id=%s kind=%s source=%s target=%s",
                context.scope.id,
                link.kind.value,
                link.source_column_id,
                link.target_column_id,
            )
        table_ids: set[UUID] = {table.id for table in context.tables}
        self._relationships.replace(table_ids, accepted)
        document: KnowledgeSubgraphWrite = assemble_scope_subgraph(
            context.scope.id,
            context.tables,
            context.relationships,
            accepted,
        )
        self._writer.write(document)
        if self._publisher is not None:
            self._publisher.publish()
        summary: str | None = proposed.summary.strip()
        if summary == "" or not include_summary:
            summary = None
        return ScopeAnalysisContext(
            scope=context.scope,
            space=context.space,
            tables=context.tables,
            existing_relationships=context.relationships,
            table_links=accepted,
            summary=summary,
            tables_without_context=list(context.tables_without_context),
            previous_links_kept=_previous_links_kept(context, accepted),
        )


def accept_table_links(context: ScopeCatalogContext, links: list[TableLink]) -> list[TableLink]:
    """Keep distinct links that stay inside the scope and do not repeat a catalog pair."""
    tables: dict[UUID, ScopeTableSummary] = {table.id: table for table in context.tables}
    columns: dict[UUID, set[UUID]] = {
        table.id: {column.id for column in table.columns} for table in context.tables
    }
    catalog_pairs: set[tuple[UUID, UUID]] = _catalog_pairs(context.relationships)
    accepted: list[TableLink] = []
    seen: set[tuple[UUID, UUID, str]] = set()
    link: TableLink
    for link in links:
        if link.source_table_id not in tables or link.target_table_id not in tables:
            raise ValidationAppError("table link uses an unknown table", field="table_link")
        if link.source_table_id == link.target_table_id:
            raise ValidationAppError("table link must join two tables", field="table_link")
        source_columns: set[UUID] = columns[link.source_table_id]
        target_columns: set[UUID] = columns[link.target_table_id]
        if link.source_column_id not in source_columns:
            raise ValidationAppError(
                "source column does not belong to the source table",
                field="table_link",
            )
        if link.target_column_id not in target_columns:
            raise ValidationAppError(
                "target column does not belong to the target table",
                field="table_link",
            )
        pair: tuple[UUID, UUID] = (link.source_column_id, link.target_column_id)
        reverse: tuple[UUID, UUID] = (link.target_column_id, link.source_column_id)
        if pair in catalog_pairs or reverse in catalog_pairs:
            raise ValidationAppError(
                "column pair is already a catalog relationship",
                field="table_link",
            )
        key: tuple[UUID, UUID, str] = (
            link.source_column_id,
            link.target_column_id,
            link.kind.value,
        )
        if key in seen:
            continue
        if len(accepted) >= _MAX_LINKS:
            raise ValidationAppError("too many table links", field="table_links")
        seen.add(key)
        accepted.append(link)
    return accepted


def assemble_scope_subgraph(
    scope_id: UUID,
    tables: list[ScopeTableSummary],
    relationships: list[TableRelation],
    links: list[TableLink],
) -> KnowledgeSubgraphWrite:
    """Build table nodes for the scope. Column nodes stay on each table's subgraph."""
    nodes: dict[UUID, KnowledgeNodeWrite] = {}
    table: ScopeTableSummary
    for table in tables:
        nodes[table.id] = KnowledgeNodeWrite(
            node_id=_node_id(scope_id, table.id),
            catalog_id=str(table.id),
            name=table.name,
        )
    edges: list[KnowledgeEdgeWrite] = []
    relation: TableRelation
    for relation in relationships:
        source: KnowledgeNodeWrite | None = nodes.get(relation.source_table_id)
        target: KnowledgeNodeWrite | None = nodes.get(relation.target_table_id)
        if source is None or target is None:
            continue
        edges.append(
            KnowledgeEdgeWrite(
                source=source.node_id,
                target=target.node_id,
                kind="REFERENCES",
                reason=_catalog_reason(relation),
            )
        )
    by_id: dict[UUID, ScopeTableSummary] = {item.id: item for item in tables}
    link: TableLink
    for link in links:
        source_node: KnowledgeNodeWrite | None = nodes.get(link.source_table_id)
        target_node: KnowledgeNodeWrite | None = nodes.get(link.target_table_id)
        if source_node is None or target_node is None:
            continue
        edges.append(
            KnowledgeEdgeWrite(
                source=source_node.node_id,
                target=target_node.node_id,
                kind=link.kind.value,
                reason=_link_reason(link, by_id),
            )
        )
    return KnowledgeSubgraphWrite(
        owner_table_id=str(scope_id),
        owner_kind="scope",
        tables=list(nodes.values()),
        columns=[],
        edges=edges,
    )


def _catalog_pairs(relationships: list[TableRelation]) -> set[tuple[UUID, UUID]]:
    pairs: set[tuple[UUID, UUID]] = set()
    relation: TableRelation
    for relation in relationships:
        if relation.origin == RelationshipOrigin.LLM:
            continue
        mapping: ColumnMappingSummary
        for mapping in relation.column_mappings:
            pairs.add((mapping.source_column_id, mapping.target_column_id))
    return pairs


def _previous_links_kept(context: ScopeCatalogContext, accepted: list[TableLink]) -> int:
    if context.previous is None:
        return 0
    prior: set[tuple[UUID, UUID, str]] = {
        (link.source_column_id, link.target_column_id, link.kind.value)
        for link in context.previous.links
    }
    kept: int = 0
    link: TableLink
    for link in accepted:
        key: tuple[UUID, UUID, str] = (
            link.source_column_id,
            link.target_column_id,
            link.kind.value,
        )
        if key in prior:
            kept += 1
    return kept


def _catalog_reason(relation: TableRelation) -> str:
    origin: str = relation.origin.value
    if relation.description is None or relation.description.strip() == "":
        return f"catalog {origin}"
    return f"catalog {origin}: {relation.description.strip()}"


def _link_reason(link: TableLink, tables: dict[UUID, ScopeTableSummary]) -> str:
    source: ScopeTableSummary = tables[link.source_table_id]
    target: ScopeTableSummary = tables[link.target_table_id]
    source_column: str = _column_name(source.columns, link.source_column_id)
    target_column: str = _column_name(target.columns, link.target_column_id)
    return f"{source.name}.{source_column} -> {target.name}.{target_column}: {link.reason}"


def _column_name(columns: list[ColumnMetadata], column_id: UUID) -> str:
    column: ColumnMetadata
    for column in columns:
        if column.id == column_id:
            return column.name
    return str(column_id)


def _node_id(scope_id: UUID, table_id: UUID) -> str:
    return f"knowledge:scope:{scope_id}:table:{table_id}"
