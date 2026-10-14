"""Turn a space catalog into cross-scope links and a space-owned subgraph."""

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
from intelegent_data_catalog.knowledge.context.scope_graph import pipeline_derived_edges
from intelegent_data_catalog.knowledge.models import (
    ColumnMappingSummary,
    ColumnMetadata,
    PipelineInput,
    ScopeTableSummary,
    SpaceAnalysisContext,
    SpaceCatalogContext,
    SpaceProposal,
    SpaceScopeSummary,
    TableLink,
    TableRelation,
    pipeline_summaries,
)
from intelegent_data_catalog.persistence.enums import RelationshipOrigin

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")

_MAX_LINKS: int = 60


class SpaceRelationProposer(Protocol):
    """Selects cross-scope links and a summary. Implementations may call a model."""

    def propose(self, context: SpaceCatalogContext) -> SpaceProposal:
        """Return links whose tables and columns come from ``context``."""
        ...


class SpaceRelationshipStore(Protocol):
    """Replaces LLM relationships that cross scopes. Intra-scope rows stay."""

    def replace_across_scopes(self, scope_of: dict[UUID, UUID], links: list[TableLink]) -> None:
        """Delete prior cross-scope LLM rows and insert ``links``."""
        ...


class SpaceProjectionPublisher(Protocol):
    """Republishes the catalog projection after PostgreSQL relationships change."""

    def publish(self) -> None:
        """Write the current projection. A missing graph store does nothing."""
        ...


class SpaceGraphAppender(GraphBuilder[SpaceCatalogContext, SpaceAnalysisContext], Protocol):
    """Accepts space links, stores them, and writes the space subgraph."""

    def apply(
        self,
        context: SpaceCatalogContext,
        *,
        include_summary: bool = True,
    ) -> SpaceAnalysisContext:
        """Propose links, store them, and return the space context."""
        ...


class SpaceGraphBuilder(SpaceGraphAppender):
    """Accept model links, store them, and write the space subgraph."""

    def __init__(
        self,
        proposer: SpaceRelationProposer,
        writer: KnowledgeSubgraphWriter,
        relationships: SpaceRelationshipStore,
        publisher: SpaceProjectionPublisher | None = None,
    ) -> None:
        self._proposer: SpaceRelationProposer = proposer
        self._writer: KnowledgeSubgraphWriter = writer
        self._relationships: SpaceRelationshipStore = relationships
        self._publisher: SpaceProjectionPublisher | None = publisher

    def apply(
        self,
        context: SpaceCatalogContext,
        *,
        include_summary: bool = True,
    ) -> SpaceAnalysisContext:
        """Propose links, replace cross-scope LLM relationships, and return the context."""
        proposed: SpaceProposal = self._proposer.propose(context)
        accepted: list[TableLink] = accept_space_links(context, proposed.links)
        link: TableLink
        for link in accepted:
            logger.info(
                "space link space_id=%s kind=%s source=%s target=%s",
                context.space.id,
                link.kind.value,
                link.source_column_id,
                link.target_column_id,
            )
        self._relationships.replace_across_scopes(_scope_of(context), accepted)
        document: KnowledgeSubgraphWrite = assemble_space_subgraph(
            context.space.id,
            context.scopes,
            context.relationships,
            accepted,
            context.pipelines,
        )
        self._writer.write(document)
        if self._publisher is not None:
            self._publisher.publish()
        summary: str | None = proposed.summary.strip()
        if summary == "" or not include_summary:
            summary = None
        return SpaceAnalysisContext(
            space=context.space,
            scopes=context.scopes,
            existing_relationships=context.relationships,
            table_links=accepted,
            summary=summary,
            scopes_without_context=list(context.scopes_without_context),
            previous_links_kept=_previous_links_kept(context, accepted),
            pipelines=pipeline_summaries(context.pipelines),
        )


def accept_space_links(context: SpaceCatalogContext, links: list[TableLink]) -> list[TableLink]:
    """Keep distinct links that cross scopes and do not repeat a catalog pair."""
    tables: dict[UUID, ScopeTableSummary] = {}
    scope_of: dict[UUID, UUID] = _scope_of(context)
    scope: SpaceScopeSummary
    for scope in context.scopes:
        table: ScopeTableSummary
        for table in scope.tables:
            tables[table.id] = table
    columns: dict[UUID, set[UUID]] = {
        table.id: {column.id for column in table.columns} for table in tables.values()
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
        if scope_of[link.source_table_id] == scope_of[link.target_table_id]:
            raise ValidationAppError("table link must cross scopes", field="table_link")
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


def assemble_space_subgraph(
    space_id: UUID,
    scopes: list[SpaceScopeSummary],
    relationships: list[TableRelation],
    links: list[TableLink],
    pipelines: list[PipelineInput] | None = None,
) -> KnowledgeSubgraphWrite:
    """Build scope and table nodes. Column nodes stay on each table's subgraph."""
    scope_nodes: dict[UUID, KnowledgeNodeWrite] = {}
    table_nodes: dict[UUID, KnowledgeNodeWrite] = {}
    scope_of: dict[UUID, UUID] = {}
    scope: SpaceScopeSummary
    for scope in scopes:
        scope_nodes[scope.id] = KnowledgeNodeWrite(
            node_id=_node_id(space_id, "scope", scope.id),
            catalog_id=str(scope.id),
            name=scope.name,
        )
        table: ScopeTableSummary
        for table in scope.tables:
            scope_of[table.id] = scope.id
            table_nodes[table.id] = KnowledgeNodeWrite(
                node_id=_node_id(space_id, "table", table.id),
                catalog_id=str(table.id),
                name=table.name,
            )
    edges: list[KnowledgeEdgeWrite] = []
    table_id: UUID
    for table_id, node in table_nodes.items():
        owner: KnowledgeNodeWrite = scope_nodes[scope_of[table_id]]
        edges.append(
            KnowledgeEdgeWrite(
                source=owner.node_id,
                target=node.node_id,
                kind="HAS_TABLE",
                reason=None,
            )
        )
    relation: TableRelation
    for relation in relationships:
        source: KnowledgeNodeWrite | None = table_nodes.get(relation.source_table_id)
        target: KnowledgeNodeWrite | None = table_nodes.get(relation.target_table_id)
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
    by_id: dict[UUID, ScopeTableSummary] = {}
    scope_names: dict[UUID, str] = {}
    for scope in scopes:
        scope_names[scope.id] = scope.name
        for table in scope.tables:
            by_id[table.id] = table
    link: TableLink
    for link in links:
        source_node: KnowledgeNodeWrite | None = table_nodes.get(link.source_table_id)
        target_node: KnowledgeNodeWrite | None = table_nodes.get(link.target_table_id)
        if source_node is None or target_node is None:
            continue
        edges.append(
            KnowledgeEdgeWrite(
                source=source_node.node_id,
                target=target_node.node_id,
                kind=link.kind.value,
                reason=_link_reason(link, by_id, scope_of, scope_names),
            )
        )
    selected: list[PipelineInput] = [] if pipelines is None else pipelines
    edges.extend(pipeline_derived_edges(table_nodes, selected))
    return KnowledgeSubgraphWrite(
        owner_table_id=str(space_id),
        owner_kind="space",
        scopes=list(scope_nodes.values()),
        tables=list(table_nodes.values()),
        columns=[],
        edges=edges,
    )


def _scope_of(context: SpaceCatalogContext) -> dict[UUID, UUID]:
    found: dict[UUID, UUID] = {}
    scope: SpaceScopeSummary
    for scope in context.scopes:
        table: ScopeTableSummary
        for table in scope.tables:
            found[table.id] = scope.id
    return found


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


def _previous_links_kept(context: SpaceCatalogContext, accepted: list[TableLink]) -> int:
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


def _link_reason(
    link: TableLink,
    tables: dict[UUID, ScopeTableSummary],
    scope_of: dict[UUID, UUID],
    scope_names: dict[UUID, str],
) -> str:
    source: ScopeTableSummary = tables[link.source_table_id]
    target: ScopeTableSummary = tables[link.target_table_id]
    source_column: str = _column_name(source.columns, link.source_column_id)
    target_column: str = _column_name(target.columns, link.target_column_id)
    source_scope: str = scope_names[scope_of[link.source_table_id]]
    target_scope: str = scope_names[scope_of[link.target_table_id]]
    return (
        f"{source_scope}.{source.name}.{source_column} -> "
        f"{target_scope}.{target.name}.{target_column}: {link.reason}"
    )


def _column_name(columns: list[ColumnMetadata], column_id: UUID) -> str:
    column: ColumnMetadata
    for column in columns:
        if column.id == column_id:
            return column.name
    return str(column_id)


def _node_id(space_id: UUID, kind: str, catalog_id: UUID) -> str:
    return f"knowledge:space:{space_id}:{kind}:{catalog_id}"
