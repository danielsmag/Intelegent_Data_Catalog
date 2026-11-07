"""Ask for important column links and write them as a Neo4j subgraph."""

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView
from intelegent_data_catalog.graph.knowledge_subgraph import (
    KnowledgeEdgeWrite,
    KnowledgeNodeWrite,
    KnowledgeSubgraphWrite,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnCandidate,
    ColumnMappingSummary,
    ColumnMetadata,
    ColumnRelation,
    ColumnRelationKind,
    TableAnalysisContext,
    TableRelation,
)
from intelegent_data_catalog.persistence.models import CatalogTableRow, ColumnRow
from intelegent_data_catalog.registry.repository import RegistryRepository

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")

_MAX_CANDIDATE_TABLES: int = 40
_MAX_CANDIDATE_COLUMNS: int = 400
_MAX_RELATIONS: int = 50
_DESCRIPTION_LIMIT: int = 200
_NEO4J_REASON: str = "Recorded in the table knowledge graph."


class ColumnCandidateSource(Protocol):
    """Supplies columns the model may link. Does not write the catalog."""

    def list_candidates(
        self,
        focus_table_id: UUID,
        extra_column_ids: set[UUID],
    ) -> list[ColumnCandidate]:
        """Return focus columns, relationship endpoints, then other catalog columns."""
        ...


@dataclass(frozen=True)
class ColumnProposalContext:
    """Analysis context plus the columns the model may link."""

    analysis: TableAnalysisContext
    columns: list[ColumnCandidate]


class ColumnRelationProposer(Protocol):
    """Selects important column relations. Implementations may call a model."""

    def propose(self, context: ColumnProposalContext) -> list[ColumnRelation]:
        """Return relations whose column ids come from ``context``."""
        ...


class KnowledgeSubgraphWriter(Protocol):
    """Replaces the Neo4j subgraph owned by one table."""

    def write(self, document: KnowledgeSubgraphWrite) -> None:
        """Write ``document`` and drop the previous subgraph for its owner."""
        ...


class GraphBuilder[ContextT, AnalysisT](Protocol):
    """Turns one catalog context into the analysis that was written."""

    def apply(self, context: ContextT) -> AnalysisT:
        """Return the analysis that names what was written."""
        ...


class ColumnGraphAppender(GraphBuilder[TableAnalysisContext, TableAnalysisContext], Protocol):
    """Adds column relations to a built context and writes the subgraph."""

    def apply(self, context: TableAnalysisContext) -> TableAnalysisContext:
        """Return the context with the relations that were written."""
        ...


class ColumnCatalog:
    """Load candidate columns from PostgreSQL. Reads do not change rows."""

    def __init__(self, repository: RegistryRepository) -> None:
        self._repository: RegistryRepository = repository

    def list_candidates(
        self,
        focus_table_id: UUID,
        extra_column_ids: set[UUID],
    ) -> list[ColumnCandidate]:
        """Prefer the focus table, then known relationship columns, then other tables."""
        focus: CatalogTableRow | None = self._repository.get_table(focus_table_id)
        if focus is None:
            raise EntityNotFoundError("table", str(focus_table_id))
        chosen: list[ColumnCandidate] = []
        seen: set[UUID] = set()
        self._append_table(chosen, seen, focus, focus_table_id)
        extra_id: UUID
        for extra_id in sorted(extra_column_ids, key=str):
            if len(chosen) >= _MAX_CANDIDATE_COLUMNS:
                break
            self._append_column_id(chosen, seen, extra_id, focus_table_id)
        others: list[CatalogTableRow] = sorted(
            (
                item
                for item in self._repository.list_all_tables()
                if item.id != focus_table_id
            ),
            key=lambda item: item.name,
        )
        table_count: int = 1
        table: CatalogTableRow
        for table in others:
            if table_count >= _MAX_CANDIDATE_TABLES or len(chosen) >= _MAX_CANDIDATE_COLUMNS:
                break
            before: int = len(chosen)
            self._append_table(chosen, seen, table, focus_table_id)
            if len(chosen) > before:
                table_count += 1
        return chosen

    def _append_table(
        self,
        chosen: list[ColumnCandidate],
        seen: set[UUID],
        table: CatalogTableRow,
        focus_table_id: UUID,
    ) -> None:
        row: ColumnRow
        for row in self._repository.list_columns(table.id):
            if len(chosen) >= _MAX_CANDIDATE_COLUMNS:
                return
            if row.id in seen:
                continue
            seen.add(row.id)
            chosen.append(_candidate(row, table.name, table.id == focus_table_id))

    def _append_column_id(
        self,
        chosen: list[ColumnCandidate],
        seen: set[UUID],
        column_id: UUID,
        focus_table_id: UUID,
    ) -> None:
        if column_id in seen or len(chosen) >= _MAX_CANDIDATE_COLUMNS:
            return
        row: ColumnRow | None = self._repository.get_column(column_id)
        if row is None:
            return
        table: CatalogTableRow | None = self._repository.get_table(row.table_id)
        if table is None:
            return
        seen.add(row.id)
        chosen.append(_candidate(row, table.name, table.id == focus_table_id))


class ColumnSubgraphBuilder(ColumnGraphAppender):
    """Turn one analysis context into a column subgraph and attach the relations."""

    def __init__(
        self,
        candidates: ColumnCandidateSource,
        proposer: ColumnRelationProposer,
        writer: KnowledgeSubgraphWriter,
    ) -> None:
        self._candidates: ColumnCandidateSource = candidates
        self._proposer: ColumnRelationProposer = proposer
        self._writer: KnowledgeSubgraphWriter = writer

    def apply(self, context: TableAnalysisContext) -> TableAnalysisContext:
        """Select relations, write the subgraph, and return the context that names them."""
        loaded: list[ColumnCandidate] = self._candidates.list_candidates(
            context.table.id,
            _relationship_column_ids(context),
        )
        columns: list[ColumnCandidate] = _with_focus_flags(context, loaded)
        logger.info(
            "column subgraph candidates table_id=%s count=%s",
            context.table.id,
            len(columns),
        )
        proposed: list[ColumnRelation] = []
        has_focus_column: bool = any(column.on_focus_table for column in columns)
        if has_focus_column:
            request: ColumnProposalContext = ColumnProposalContext(context, columns)
            proposed = self._proposer.propose(request)
        accepted: list[ColumnRelation] = accept_column_relations(
            context.table.id,
            columns,
            proposed,
        )
        relation: ColumnRelation
        for relation in accepted:
            logger.info(
                "column subgraph relation table_id=%s kind=%s source=%s target=%s",
                context.table.id,
                relation.kind.value,
                relation.source_column_id,
                relation.target_column_id,
            )
        document: KnowledgeSubgraphWrite = assemble_column_subgraph(
            context.table.id,
            context.table.name,
            columns,
            accepted,
        )
        self._writer.write(document)
        return context.model_copy(update={"column_relations": accepted})


def accept_column_relations(
    focus_table_id: UUID,
    columns: list[ColumnCandidate],
    relations: list[ColumnRelation],
) -> list[ColumnRelation]:
    """Keep distinct relations that touch the focus table and use known columns."""
    by_id: dict[UUID, ColumnCandidate] = {item.column_id: item for item in columns}
    accepted: list[ColumnRelation] = []
    seen: set[tuple[UUID, UUID, str]] = set()
    relation: ColumnRelation
    for relation in relations:
        source: ColumnCandidate | None = by_id.get(relation.source_column_id)
        target: ColumnCandidate | None = by_id.get(relation.target_column_id)
        if source is None or target is None:
            raise ValidationAppError("column relation uses an unknown column", field="column_id")
        if relation.source_column_id == relation.target_column_id:
            raise ValidationAppError("column relation must link two columns", field="column_id")
        source_on_focus: bool = _on_focus(source, focus_table_id)
        target_on_focus: bool = _on_focus(target, focus_table_id)
        if not source_on_focus and not target_on_focus:
            raise ValidationAppError(
                "column relation must touch the focus table",
                field="column_id",
            )
        key: tuple[UUID, UUID, str] = (
            relation.source_column_id,
            relation.target_column_id,
            relation.kind.value,
        )
        if key in seen:
            continue
        if len(accepted) >= _MAX_RELATIONS:
            raise ValidationAppError("too many column relations", field="column_relations")
        seen.add(key)
        accepted.append(relation)
    return accepted


def assemble_column_subgraph(
    focus_table_id: UUID,
    focus_table_name: str,
    columns: list[ColumnCandidate],
    relations: list[ColumnRelation],
) -> KnowledgeSubgraphWrite:
    """Build nodes for the focus table only.

    A relation that touches another table stays on the context and is not drawn.
    The scope agent owns that other table and its columns.
    """
    owner: str = str(focus_table_id)
    tables: dict[UUID, KnowledgeNodeWrite] = {
        focus_table_id: _table_node(owner, focus_table_id, focus_table_name),
    }
    column_nodes: dict[UUID, KnowledgeNodeWrite] = {}
    edges: list[KnowledgeEdgeWrite] = []
    seen_columns: set[tuple[str, str]] = set()
    by_id: dict[UUID, ColumnCandidate] = {item.column_id: item for item in columns}
    column: ColumnCandidate
    for column in columns:
        if _on_focus(column, focus_table_id):
            _add_column(owner, tables, column_nodes, edges, seen_columns, column)
    relation: ColumnRelation
    for relation in relations:
        source: ColumnCandidate = by_id[relation.source_column_id]
        target: ColumnCandidate = by_id[relation.target_column_id]
        if not _on_focus(source, focus_table_id) or not _on_focus(target, focus_table_id):
            continue
        _add_column(owner, tables, column_nodes, edges, seen_columns, source)
        _add_column(owner, tables, column_nodes, edges, seen_columns, target)
        edges.append(
            KnowledgeEdgeWrite(
                source=column_nodes[relation.source_column_id].node_id,
                target=column_nodes[relation.target_column_id].node_id,
                kind=relation.kind.value,
                reason=relation.reason,
            )
        )
    return KnowledgeSubgraphWrite(
        owner_table_id=owner,
        tables=list(tables.values()),
        columns=list(column_nodes.values()),
        edges=edges,
    )


def _relationship_column_ids(context: TableAnalysisContext) -> set[UUID]:
    found: set[UUID] = set()
    relation: TableRelation
    for relation in context.existing_relationships:
        mapping: ColumnMappingSummary
        for mapping in relation.column_mappings:
            found.add(mapping.source_column_id)
            found.add(mapping.target_column_id)
    return found


def _with_focus_flags(
    context: TableAnalysisContext,
    columns: list[ColumnCandidate],
) -> list[ColumnCandidate]:
    known: dict[UUID, ColumnMetadata] = {column.id: column for column in context.columns}
    merged: list[ColumnCandidate] = []
    item: ColumnCandidate
    for item in columns:
        column: ColumnMetadata | None = known.get(item.column_id)
        if column is None:
            merged.append(item)
            continue
        description: str | None = (
            column.description or column.source_description or item.description
        )
        merged.append(
            item.model_copy(
                update={
                    "is_foreign_key": column.is_foreign_key,
                    "description": _short_text(description),
                    "on_focus_table": True,
                }
            )
        )
    return merged


def _add_column(
    owner: str,
    tables: dict[UUID, KnowledgeNodeWrite],
    column_nodes: dict[UUID, KnowledgeNodeWrite],
    edges: list[KnowledgeEdgeWrite],
    seen_columns: set[tuple[str, str]],
    column: ColumnCandidate,
) -> None:
    if column.table_id not in tables:
        tables[column.table_id] = _table_node(owner, column.table_id, column.table_name)
    if column.column_id not in column_nodes:
        column_nodes[column.column_id] = KnowledgeNodeWrite(
            node_id=_node_id(owner, "column", column.column_id),
            catalog_id=str(column.column_id),
            name=column.name,
        )
    table_node: str = tables[column.table_id].node_id
    column_node: str = column_nodes[column.column_id].node_id
    key: tuple[str, str] = (table_node, column_node)
    if key in seen_columns:
        return
    seen_columns.add(key)
    edges.append(
        KnowledgeEdgeWrite(
            source=table_node,
            target=column_node,
            kind="HAS_COLUMN",
            reason=None,
        )
    )


def _table_node(owner: str, table_id: UUID, name: str) -> KnowledgeNodeWrite:
    return KnowledgeNodeWrite(
        node_id=_node_id(owner, "table", table_id),
        catalog_id=str(table_id),
        name=name,
    )


def _node_id(owner: str, kind: str, catalog_id: UUID) -> str:
    return f"knowledge:{owner}:{kind}:{catalog_id}"


def _on_focus(column: ColumnCandidate, focus_table_id: UUID) -> bool:
    return column.on_focus_table or column.table_id == focus_table_id


def _candidate(row: ColumnRow, table_name: str, on_focus_table: bool) -> ColumnCandidate:
    return ColumnCandidate(
        column_id=row.id,
        table_id=row.table_id,
        table_name=table_name,
        name=row.name,
        data_type=row.data_type,
        is_primary_key=row.is_primary_key,
        is_unique=row.is_unique,
        is_foreign_key=False,
        on_focus_table=on_focus_table,
        description=_short_text(row.description or row.source_description),
        context=_short_text(row.context),
    )


def _short_text(value: str | None) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    if len(text) <= _DESCRIPTION_LIMIT:
        return text
    return text[:_DESCRIPTION_LIMIT]


class TableKnowledgeSlice:
    """Column relations from one reader, and whether that reader had a record."""

    def __init__(
        self,
        relations: list[ColumnRelation],
        present: bool,
        origin: str,
    ) -> None:
        self.relations: list[ColumnRelation] = relations
        self.present: bool = present
        self.origin: str = origin


class KnowledgeGraphReader(Protocol):
    """Reads one table's Neo4j knowledge subgraph."""

    def read_knowledge(self, owner_table_id: str) -> GraphView:
        """Return the subgraph owned by the catalog table id."""
        ...


class Neo4jTableKnowledgeReader:
    """Column relations from the table's Neo4j knowledge subgraph."""

    def __init__(self, graphs: KnowledgeGraphReader) -> None:
        self._graphs: KnowledgeGraphReader = graphs

    def read(self, table_id: UUID) -> TableKnowledgeSlice:
        """Map subgraph edges onto column relations. ``HAS_COLUMN`` is ignored."""
        view: GraphView = self._graphs.read_knowledge(str(table_id))
        relations: list[ColumnRelation] = column_relations_from_graph(view)
        return TableKnowledgeSlice(
            relations=relations,
            present=len(view.nodes) > 0,
            origin="neo4j",
        )


def column_relations_from_graph(view: GraphView) -> list[ColumnRelation]:
    """Turn knowledge-subgraph edges into column relations. Hierarchy edges are skipped."""
    nodes: dict[str, GraphNode] = {node.id: node for node in view.nodes}
    relations: list[ColumnRelation] = []
    seen: set[tuple[UUID, UUID, str]] = set()
    edge: GraphEdge
    for edge in view.edges:
        kind: ColumnRelationKind | None = _relation_kind(edge.kind)
        if kind is None:
            continue
        source: GraphNode | None = nodes.get(edge.source)
        target: GraphNode | None = nodes.get(edge.target)
        if source is None or target is None:
            continue
        if source.kind != "column" or target.kind != "column":
            continue
        source_id: UUID | None = _catalog_uuid(source.catalog_id)
        target_id: UUID | None = _catalog_uuid(target.catalog_id)
        if source_id is None or target_id is None or source_id == target_id:
            continue
        key: tuple[UUID, UUID, str] = (source_id, target_id, kind.value)
        if key in seen:
            continue
        seen.add(key)
        reason: str = edge.reason.strip() if edge.reason is not None else ""
        relations.append(
            ColumnRelation(
                source_column_id=source_id,
                target_column_id=target_id,
                kind=kind,
                reason=reason if reason != "" else _NEO4J_REASON,
            )
        )
    return relations


def _relation_kind(kind: str) -> ColumnRelationKind | None:
    match kind:
        case "REFERENCES":
            return ColumnRelationKind.REFERENCES
        case "JOINS_ON":
            return ColumnRelationKind.JOINS_ON
        case "DERIVED_FROM":
            return ColumnRelationKind.DERIVED_FROM
        case "SAME_MEANING":
            return ColumnRelationKind.SAME_MEANING
        case _:
            return None


def _catalog_uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
