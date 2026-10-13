"""Load one scope for the scope agent. Reads do not change catalog rows."""

import logging
from typing import Literal, Protocol
from uuid import UUID

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import EntityNotFoundError, GraphUnavailableError
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView
from intelegent_data_catalog.knowledge.context.catalog import (
    _column,
    _foreign_key_sources,
    _relation,
    _scope_summary,
    _space_summary,
)
from intelegent_data_catalog.knowledge.context.pipelines import AttachedPipelineSource
from intelegent_data_catalog.knowledge.models import (
    ColumnMetadata,
    ColumnRelation,
    ColumnRelationKind,
    PipelineInput,
    PreviousScopeFindings,
    ScopeCatalogContext,
    ScopeContextOptions,
    ScopeTableSummary,
    TableAnalysisContext,
    TableKnowledgeInput,
    TableRelation,
)
from intelegent_data_catalog.knowledge.store import SavedScopeContext
from intelegent_data_catalog.persistence.enums import RelationshipOrigin, TableStatus
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    ColumnRow,
    RelationshipRow,
    ScopeRow,
    SpaceRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")

_MAX_TABLES: int = 40
_MAX_COLUMNS: int = 400
_MAX_TABLE_RELATIONS: int = 50
_DESCRIPTION_LIMIT: int = 200
_NEO4J_REASON: str = "Recorded in the table knowledge graph."
KnowledgeSource = Literal["postgres", "neo4j", "both"]


class TableKnowledgeSlice:
    """Column relations from one reader, and whether that reader had a record."""

    def __init__(
        self,
        relations: list[ColumnRelation],
        present: bool,
        origin: Literal["postgres", "neo4j"],
    ) -> None:
        self.relations: list[ColumnRelation] = relations
        self.present: bool = present
        self.origin: Literal["postgres", "neo4j"] = origin


class MergedTableKnowledge:
    """Column relations merged across readers. ``source`` is none when nothing was stored."""

    def __init__(self, relations: list[ColumnRelation], source: KnowledgeSource | None) -> None:
        self.relations: list[ColumnRelation] = relations
        self.source: KnowledgeSource | None = source


class SavedTableContextReader(Protocol):
    """Reads the last table analysis snapshot."""

    def get(self, table_id: UUID) -> TableAnalysisContext | None:
        """Return the snapshot, or none when the table has never been built."""
        ...


class TableKnowledgeReader(Protocol):
    """Supplies column relations already stored for one table."""

    def read(self, table_id: UUID) -> TableKnowledgeSlice:
        """Return relations. ``present`` is false when this source has no record."""
        ...


class KnowledgeGraphReader(Protocol):
    """Reads one table's Neo4j knowledge subgraph."""

    def read_knowledge(self, owner_table_id: str) -> GraphView:
        """Return the subgraph owned by the catalog table id."""
        ...


class PreviousScopeReader(Protocol):
    """Reads the last scope snapshot."""

    def saved(self, scope_id: UUID) -> SavedScopeContext | None:
        """Return the snapshot, or none when the scope has never been built."""
        ...


class ScopeTableKnowledgeReader(Protocol):
    """Merged table knowledge used while a scope context is loaded."""

    def read(self, table_id: UUID) -> MergedTableKnowledge:
        """Return deduped relations and which stores contributed them."""
        ...


class ScopeCatalogContextProvider(Protocol):
    """Supplies canonical scope metadata. Implementations do not call a model."""

    def get_scope_context(
        self,
        scope_id: UUID,
        options: ScopeContextOptions | None = None,
    ) -> ScopeCatalogContext:
        """Return tables, relationships, table knowledge, and the previous scope build."""
        ...


class PostgresTableKnowledgeReader:
    """Column relations from the saved table analysis context."""

    def __init__(self, contexts: SavedTableContextReader) -> None:
        self._contexts: SavedTableContextReader = contexts

    def read(self, table_id: UUID) -> TableKnowledgeSlice:
        """Return relations from PostgreSQL. A missing snapshot is not present."""
        loaded: TableAnalysisContext | None = self._contexts.get(table_id)
        if loaded is None:
            return TableKnowledgeSlice(relations=[], present=False, origin="postgres")
        relations: list[ColumnRelation] = list(loaded.column_relations)
        return TableKnowledgeSlice(relations=relations, present=True, origin="postgres")


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


class MergedTableKnowledgeReader:
    """Union of table-knowledge readers. A Neo4j outage keeps the other results."""

    def __init__(self, readers: list[TableKnowledgeReader]) -> None:
        self._readers: list[TableKnowledgeReader] = readers

    def read(self, table_id: UUID) -> MergedTableKnowledge:
        """Dedupe relations by column pair and kind."""
        collected: list[TableKnowledgeSlice] = []
        reader: TableKnowledgeReader
        for reader in self._readers:
            try:
                collected.append(reader.read(table_id))
            except GraphUnavailableError:
                logger.warning("table knowledge read failed table_id=%s", table_id)
        return _merge_slices(collected)


class PostgresScopeCatalogContextProvider:
    """Read one scope from PostgreSQL, saved table contexts, and the previous scope build."""

    def __init__(
        self,
        repository: RegistryRepository,
        knowledge: ScopeTableKnowledgeReader,
        previous: PreviousScopeReader,
        pipelines: AttachedPipelineSource | None = None,
    ) -> None:
        self._repository: RegistryRepository = repository
        self._knowledge: ScopeTableKnowledgeReader = knowledge
        self._previous: PreviousScopeReader = previous
        self._pipelines: AttachedPipelineSource | None = pipelines

    def get_scope_context(
        self,
        scope_id: UUID,
        options: ScopeContextOptions | None = None,
    ) -> ScopeCatalogContext:
        """Load the scope. Tables without a built context stay in the result."""
        selected: ScopeContextOptions = options or ScopeContextOptions()
        scope: ScopeRow | None = self._repository.get_scope(scope_id)
        if scope is None:
            raise EntityNotFoundError("scope", str(scope_id))
        space: SpaceRow | None = self._repository.get_space(scope.space_id)
        if space is None:
            raise EntityNotFoundError("space", str(scope.space_id))
        rows: list[CatalogTableRow] = self._repository.list_tables(scope_id)[:_MAX_TABLES]
        table_ids: set[UUID] = {row.id for row in rows}
        relationships: list[TableRelation] = _scope_relationships(self._repository, table_ids)
        foreign_keys: set[UUID] = _foreign_key_sources(relationships)
        tables: list[ScopeTableSummary] = []
        knowledge: list[TableKnowledgeInput] = []
        missing: list[UUID] = []
        remaining: int = _MAX_COLUMNS
        row: CatalogTableRow
        for row in rows:
            columns: list[ColumnMetadata]
            columns, remaining = _columns(self._repository, row.id, foreign_keys, remaining)
            tables.append(_table_summary(row, columns))
            if not selected.include_table_knowledge:
                continue
            loaded: MergedTableKnowledge = self._knowledge.read(row.id)
            if loaded.source is None:
                missing.append(row.id)
                continue
            knowledge.append(
                TableKnowledgeInput(
                    table_id=row.id,
                    column_relations=loaded.relations[:_MAX_TABLE_RELATIONS],
                    source=loaded.source,
                )
            )
        pipelines: list[PipelineInput] = []
        if selected.include_airflow and self._pipelines is not None:
            pipelines = self._pipelines.for_scope(scope_id, table_ids)
        return ScopeCatalogContext(
            scope=_scope_summary(scope),
            space=_space_summary(space),
            tables=tables,
            relationships=relationships,
            table_knowledge=knowledge,
            tables_without_context=missing,
            previous=_previous_findings(self._previous, scope_id, selected.include_previous),
            pipelines=pipelines,
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


def _merge_slices(collected: list[TableKnowledgeSlice]) -> MergedTableKnowledge:
    relations: list[ColumnRelation] = []
    seen: set[tuple[UUID, UUID, str]] = set()
    origins: set[str] = set()
    item: TableKnowledgeSlice
    for item in collected:
        if item.present:
            origins.add(item.origin)
        relation: ColumnRelation
        for relation in item.relations:
            key: tuple[UUID, UUID, str] = (
                relation.source_column_id,
                relation.target_column_id,
                relation.kind.value,
            )
            if key in seen:
                continue
            seen.add(key)
            relations.append(relation)
    source: KnowledgeSource | None = _source_name(origins)
    return MergedTableKnowledge(relations=relations, source=source)


def _source_name(origins: set[str]) -> KnowledgeSource | None:
    if "postgres" in origins and "neo4j" in origins:
        return "both"
    if "postgres" in origins:
        return "postgres"
    if "neo4j" in origins:
        return "neo4j"
    return None


def _scope_relationships(
    repository: RegistryRepository,
    table_ids: set[UUID],
) -> list[TableRelation]:
    relationships: list[TableRelation] = []
    seen: set[UUID] = set()
    table_id: UUID
    for table_id in table_ids:
        row: RelationshipRow
        for row in repository.list_relationships_for_table(table_id):
            if row.id in seen:
                continue
            if row.source_table_id not in table_ids or row.target_table_id not in table_ids:
                continue
            if row.origin == RelationshipOrigin.LLM.value:
                continue
            seen.add(row.id)
            relationships.append(_relation(row, repository.list_mappings(row.id)))
    relationships.sort(key=lambda item: str(item.id))
    return relationships


def _columns(
    repository: RegistryRepository,
    table_id: UUID,
    foreign_keys: set[UUID],
    remaining: int,
) -> tuple[list[ColumnMetadata], int]:
    chosen: list[ColumnMetadata] = []
    left: int = remaining
    row: ColumnRow
    for row in repository.list_columns(table_id):
        if left <= 0:
            break
        column: ColumnMetadata = _column(row, foreign_keys)
        chosen.append(
            column.model_copy(
                update={
                    "description": _short_text(column.description),
                    "context": _short_text(column.context),
                }
            )
        )
        left -= 1
    return chosen, left


def _table_summary(row: CatalogTableRow, columns: list[ColumnMetadata]) -> ScopeTableSummary:
    description: str | None = _short_text(row.description or row.source_description)
    return ScopeTableSummary(
        id=row.id,
        name=row.name,
        display_name=row.display_name,
        description=description,
        status=TableStatus(row.status),
        row_count=row.row_count,
        context=_short_text(row.context),
        columns=columns,
    )


def _previous_findings(
    previous: PreviousScopeReader,
    scope_id: UUID,
    include_previous: bool,
) -> PreviousScopeFindings | None:
    if not include_previous:
        return None
    saved: SavedScopeContext | None = previous.saved(scope_id)
    if saved is None:
        return None
    return PreviousScopeFindings(
        summary=saved.context.summary,
        links=list(saved.context.table_links),
        built_at=saved.built_at,
    )


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


def _short_text(value: str | None) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    if len(text) <= _DESCRIPTION_LIMIT:
        return text
    return text[:_DESCRIPTION_LIMIT]
