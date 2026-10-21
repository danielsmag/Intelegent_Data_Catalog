"""Load one space for the space agent. Reads do not change catalog rows."""

import logging
from typing import Literal, Protocol
from uuid import UUID

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import EntityNotFoundError, GraphUnavailableError
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView
from intelegent_data_catalog.knowledge.context.catalog import (
    _foreign_key_sources,
    _relation,
    _space_summary,
)
from intelegent_data_catalog.knowledge.context.pipelines import AttachedPipelineSource
from intelegent_data_catalog.knowledge.context.scope_catalog import (
    _asset_summaries,
    _columns,
    _short_text,
    _table_summary,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnMetadata,
    PipelineInput,
    PreviousSpaceFindings,
    ScopeAnalysisContext,
    ScopeGraphEdge,
    ScopeKnowledgeInput,
    ScopeTableSummary,
    SpaceCatalogContext,
    SpaceContextOptions,
    SpaceScopeSummary,
    TableLink,
    TableLinkKind,
    TableRelation,
)
from intelegent_data_catalog.knowledge.store import SavedSpaceContext
from intelegent_data_catalog.persistence.enums import RelationshipOrigin
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    RelationshipRow,
    ScopeRow,
    SpaceRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")

_MAX_SCOPE_EDGES: int = 50
_REASON_LIMIT: int = 280
KnowledgeSource = Literal["postgres", "neo4j", "both"]
_GRAPH_KINDS: dict[str, TableLinkKind] = {
    "REFERENCES": TableLinkKind.REFERENCES,
    "JOINS_ON": TableLinkKind.JOINS_ON,
}


class ScopeKnowledgeSlice:
    """Scope findings from one reader, and whether that reader had a record."""

    def __init__(
        self,
        summary: str | None,
        links: list[TableLink],
        graph: list[ScopeGraphEdge],
        present: bool,
        origin: Literal["postgres", "neo4j"],
    ) -> None:
        self.summary: str | None = summary
        self.links: list[TableLink] = links
        self.graph: list[ScopeGraphEdge] = graph
        self.present: bool = present
        self.origin: Literal["postgres", "neo4j"] = origin


class MergedScopeKnowledge:
    """Scope findings merged across readers. ``source`` is none when nothing was stored."""

    def __init__(
        self,
        summary: str | None,
        links: list[TableLink],
        graph: list[ScopeGraphEdge],
        source: KnowledgeSource | None,
    ) -> None:
        self.summary: str | None = summary
        self.links: list[TableLink] = links
        self.graph: list[ScopeGraphEdge] = graph
        self.source: KnowledgeSource | None = source


class ScopeSnapshotReader(Protocol):
    """Reads the last scope analysis snapshot."""

    def get(self, scope_id: UUID) -> ScopeAnalysisContext | None:
        """Return the snapshot, or none when the scope has never been built."""
        ...


class ScopeKnowledgeReader(Protocol):
    """Supplies findings already stored for one scope."""

    def read(self, scope_id: UUID) -> ScopeKnowledgeSlice:
        """Return findings. ``present`` is false when this source has no record."""
        ...


class ScopeGraphReader(Protocol):
    """Reads one scope's Neo4j knowledge subgraph."""

    def read_owned(self, owner_property: str, owner_id: str) -> GraphView:
        """Return the subgraph stored on ``owner_property``."""
        ...


class PreviousSpaceReader(Protocol):
    """Reads the last space snapshot."""

    def saved(self, space_id: UUID) -> SavedSpaceContext | None:
        """Return the snapshot, or none when the space has never been built."""
        ...


class SpaceScopeKnowledgeReader(Protocol):
    """Merged scope knowledge used while a space context is loaded."""

    def read(self, scope_id: UUID) -> MergedScopeKnowledge:
        """Return deduped findings and which stores contributed them."""
        ...


class SpaceCatalogContextProvider(Protocol):
    """Supplies canonical space metadata. Implementations do not call a model."""

    def get_space_context(
        self,
        space_id: UUID,
        options: SpaceContextOptions | None = None,
    ) -> SpaceCatalogContext:
        """Return scopes, cross-scope relationships, scope graphs, and the previous build."""
        ...


class PostgresScopeKnowledgeReader:
    """Summary and column links from the saved scope analysis context."""

    def __init__(self, contexts: ScopeSnapshotReader) -> None:
        self._contexts: ScopeSnapshotReader = contexts

    def read(self, scope_id: UUID) -> ScopeKnowledgeSlice:
        """Return the snapshot. A missing row is not present."""
        loaded: ScopeAnalysisContext | None = self._contexts.get(scope_id)
        if loaded is None:
            return ScopeKnowledgeSlice(
                summary=None,
                links=[],
                graph=[],
                present=False,
                origin="postgres",
            )
        return ScopeKnowledgeSlice(
            summary=loaded.summary,
            links=list(loaded.table_links),
            graph=[],
            present=True,
            origin="postgres",
        )


class Neo4jScopeKnowledgeReader:
    """Table edges from the scope's Neo4j knowledge subgraph."""

    def __init__(self, graphs: ScopeGraphReader) -> None:
        self._graphs: ScopeGraphReader = graphs

    def read(self, scope_id: UUID) -> ScopeKnowledgeSlice:
        """Map subgraph edges onto table links. Hierarchy edges are ignored."""
        view: GraphView = self._graphs.read_owned("owner_scope_id", str(scope_id))
        edges: list[ScopeGraphEdge] = scope_graph_edges_from_view(view)
        return ScopeKnowledgeSlice(
            summary=None,
            links=[],
            graph=edges,
            present=len(view.nodes) > 0,
            origin="neo4j",
        )


class MergedScopeKnowledgeReader:
    """Union of scope-knowledge readers. A Neo4j outage keeps the other results."""

    def __init__(self, readers: list[ScopeKnowledgeReader]) -> None:
        self._readers: list[ScopeKnowledgeReader] = readers

    def read(self, scope_id: UUID) -> MergedScopeKnowledge:
        """Dedupe links and drop graph edges that repeat a stored link."""
        collected: list[ScopeKnowledgeSlice] = []
        reader: ScopeKnowledgeReader
        for reader in self._readers:
            try:
                collected.append(reader.read(scope_id))
            except GraphUnavailableError:
                logger.warning("scope knowledge read failed scope_id=%s", scope_id)
        return _merge_slices(collected)


class PostgresSpaceCatalogContextProvider:
    """Read one space from PostgreSQL, saved scope contexts, and each scope graph."""

    def __init__(
        self,
        repository: RegistryRepository,
        knowledge: SpaceScopeKnowledgeReader,
        previous: PreviousSpaceReader,
        pipelines: AttachedPipelineSource | None = None,
        max_scopes: int = 20,
        max_tables: int = 60,
        max_columns: int = 600,
    ) -> None:
        self._repository: RegistryRepository = repository
        self._knowledge: SpaceScopeKnowledgeReader = knowledge
        self._previous: PreviousSpaceReader = previous
        self._pipelines: AttachedPipelineSource | None = pipelines
        self._max_scopes: int = max_scopes
        self._max_tables: int = max_tables
        self._max_columns: int = max_columns

    def get_space_context(
        self,
        space_id: UUID,
        options: SpaceContextOptions | None = None,
    ) -> SpaceCatalogContext:
        """Load the space. Scopes without a built context stay in the result."""
        selected: SpaceContextOptions = options or SpaceContextOptions()
        space: SpaceRow | None = self._repository.get_space(space_id)
        if space is None:
            raise EntityNotFoundError("space", str(space_id))
        scope_rows: list[tuple[ScopeRow, list[CatalogTableRow]]] = _bounded_scopes(
            self._repository,
            space_id,
            self._max_scopes,
            self._max_tables,
        )
        table_scope: dict[UUID, UUID] = {
            table.id: scope.id for scope, tables in scope_rows for table in tables
        }
        relationships: list[TableRelation] = _cross_scope_relationships(
            self._repository,
            table_scope,
        )
        foreign_keys: set[UUID] = _foreign_key_sources(
            _all_relationships(self._repository, table_scope)
        )
        scopes: list[SpaceScopeSummary] = []
        knowledge: list[ScopeKnowledgeInput] = []
        missing: list[UUID] = []
        remaining: int = self._max_columns
        item: tuple[ScopeRow, list[CatalogTableRow]]
        for item in scope_rows:
            scope_row: ScopeRow = item[0]
            table_rows: list[CatalogTableRow] = item[1]
            tables, remaining = _scope_tables(
                self._repository,
                table_rows,
                foreign_keys,
                remaining,
            )
            scopes.append(_scope_summary(scope_row, tables))
            if not selected.include_scope_knowledge:
                continue
            loaded: MergedScopeKnowledge = self._knowledge.read(scope_row.id)
            if loaded.source is None:
                missing.append(scope_row.id)
                continue
            knowledge.append(
                ScopeKnowledgeInput(
                    scope_id=scope_row.id,
                    summary=loaded.summary,
                    links=loaded.links[:_MAX_SCOPE_EDGES],
                    graph=loaded.graph[:_MAX_SCOPE_EDGES],
                    source=loaded.source,
                )
            )
        pipelines: list[PipelineInput] = []
        if selected.include_airflow and self._pipelines is not None:
            scope_ids: set[UUID] = {item[0].id for item in scope_rows}
            pipelines = self._pipelines.for_space(space_id, scope_ids, set(table_scope))
        return SpaceCatalogContext(
            space=_space_summary(space),
            scopes=scopes,
            relationships=relationships,
            scope_knowledge=knowledge,
            scopes_without_context=missing,
            previous=_previous_findings(self._previous, space_id, selected.include_previous),
            pipelines=pipelines,
            assets=_asset_summaries(self._repository.session, scope_id=None, space_id=space_id),
        )


def scope_graph_edges_from_view(view: GraphView) -> list[ScopeGraphEdge]:
    """Turn a scope knowledge subgraph into table edges. Hierarchy edges are skipped."""
    nodes: dict[str, GraphNode] = {node.id: node for node in view.nodes}
    edges: list[ScopeGraphEdge] = []
    seen: set[tuple[UUID, UUID, str]] = set()
    edge: GraphEdge
    for edge in view.edges:
        kind: TableLinkKind | None = _GRAPH_KINDS.get(edge.kind)
        if kind is None:
            continue
        source: GraphNode | None = nodes.get(edge.source)
        target: GraphNode | None = nodes.get(edge.target)
        if source is None or target is None:
            continue
        if source.kind != "table" or target.kind != "table":
            continue
        source_id: UUID | None = _catalog_uuid(source.catalog_id)
        target_id: UUID | None = _catalog_uuid(target.catalog_id)
        if source_id is None or target_id is None or source_id == target_id:
            continue
        key: tuple[UUID, UUID, str] = (source_id, target_id, kind.value)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            ScopeGraphEdge(
                source_table_id=source_id,
                target_table_id=target_id,
                kind=kind,
                reason=_reason(edge.reason),
            )
        )
    return edges


def _merge_slices(collected: list[ScopeKnowledgeSlice]) -> MergedScopeKnowledge:
    links: list[TableLink] = []
    seen_links: set[tuple[UUID, UUID, str]] = set()
    graph: list[ScopeGraphEdge] = []
    origins: set[str] = set()
    summary: str | None = None
    item: ScopeKnowledgeSlice
    for item in collected:
        if item.present:
            origins.add(item.origin)
        if item.origin == "postgres" and item.present:
            summary = item.summary
        link: TableLink
        for link in item.links:
            link_key: tuple[UUID, UUID, str] = (
                link.source_column_id,
                link.target_column_id,
                link.kind.value,
            )
            if link_key in seen_links:
                continue
            seen_links.add(link_key)
            links.append(link)
    covered: set[tuple[UUID, UUID, str]] = {
        (link.source_table_id, link.target_table_id, link.kind.value) for link in links
    }
    seen_edges: set[tuple[UUID, UUID, str]] = set()
    for item in collected:
        edge: ScopeGraphEdge
        for edge in item.graph:
            edge_key: tuple[UUID, UUID, str] = (
                edge.source_table_id,
                edge.target_table_id,
                edge.kind.value,
            )
            if edge_key in covered or edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            graph.append(edge)
    return MergedScopeKnowledge(
        summary=summary,
        links=links,
        graph=graph,
        source=_source_name(origins),
    )


def _source_name(origins: set[str]) -> KnowledgeSource | None:
    if "postgres" in origins and "neo4j" in origins:
        return "both"
    if "postgres" in origins:
        return "postgres"
    if "neo4j" in origins:
        return "neo4j"
    return None


def _bounded_scopes(
    repository: RegistryRepository,
    space_id: UUID,
    max_scopes: int,
    max_tables: int,
) -> list[tuple[ScopeRow, list[CatalogTableRow]]]:
    chosen: list[tuple[ScopeRow, list[CatalogTableRow]]] = []
    remaining: int = max_tables
    scope: ScopeRow
    for scope in repository.list_scopes(space_id)[:max_scopes]:
        if remaining <= 0:
            break
        tables: list[CatalogTableRow] = repository.list_tables(scope.id)[:remaining]
        remaining -= len(tables)
        chosen.append((scope, tables))
    return chosen


def _scope_tables(
    repository: RegistryRepository,
    rows: list[CatalogTableRow],
    foreign_keys: set[UUID],
    remaining: int,
) -> tuple[list[ScopeTableSummary], int]:
    tables: list[ScopeTableSummary] = []
    left: int = remaining
    row: CatalogTableRow
    for row in rows:
        columns: list[ColumnMetadata]
        columns, left = _columns(repository, row.id, foreign_keys, left)
        tables.append(_table_summary(row, columns))
    return tables, left


def _scope_summary(row: ScopeRow, tables: list[ScopeTableSummary]) -> SpaceScopeSummary:
    return SpaceScopeSummary(
        id=row.id,
        name=row.name,
        slug=row.slug,
        description=_short_text(row.description),
        context=_short_text(row.context),
        tables=tables,
    )


def _all_relationships(
    repository: RegistryRepository,
    table_scope: dict[UUID, UUID],
) -> list[TableRelation]:
    relationships: list[TableRelation] = []
    row: RelationshipRow
    for row in repository.list_relationships_between(set(table_scope)):
        relationships.append(_relation(row, repository.list_mappings(row.id)))
    return relationships


def _cross_scope_relationships(
    repository: RegistryRepository,
    table_scope: dict[UUID, UUID],
) -> list[TableRelation]:
    relationships: list[TableRelation] = []
    row: RelationshipRow
    for row in repository.list_relationships_between(set(table_scope)):
        source_scope: UUID | None = table_scope.get(row.source_table_id)
        target_scope: UUID | None = table_scope.get(row.target_table_id)
        if source_scope is None or target_scope is None or source_scope == target_scope:
            continue
        if row.origin == RelationshipOrigin.LLM.value:
            continue
        relationships.append(_relation(row, repository.list_mappings(row.id)))
    relationships.sort(key=lambda item: str(item.id))
    return relationships


def _previous_findings(
    previous: PreviousSpaceReader,
    space_id: UUID,
    include_previous: bool,
) -> PreviousSpaceFindings | None:
    if not include_previous:
        return None
    saved: SavedSpaceContext | None = previous.saved(space_id)
    if saved is None:
        return None
    return PreviousSpaceFindings(
        summary=saved.context.summary,
        links=list(saved.context.table_links),
        built_at=saved.built_at,
    )


def _catalog_uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _reason(value: str | None) -> str:
    if value is None:
        return ""
    text: str = value.strip()
    return text[:_REASON_LIMIT]
