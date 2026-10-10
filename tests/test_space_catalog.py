"""Merged scope knowledge and the space catalog provider."""

from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import GraphUnavailableError
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView
from intelegent_data_catalog.knowledge.context.space_catalog import (
    MergedScopeKnowledge,
    MergedScopeKnowledgeReader,
    Neo4jScopeKnowledgeReader,
    PostgresScopeKnowledgeReader,
    PostgresSpaceCatalogContextProvider,
    ScopeKnowledgeSlice,
    scope_graph_edges_from_view,
)
from intelegent_data_catalog.knowledge.models import (
    ScopeAnalysisContext,
    ScopeSummary,
    SpaceAnalysisContext,
    SpaceCatalogContext,
    SpaceSummary,
    TableLinkKind,
)
from intelegent_data_catalog.knowledge.store import (
    ScopeKnowledgeContextStore,
    SpaceKnowledgeContextStore,
)
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import DataSourceType, TableKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    CatalogTableCreate,
    DataSourceCreate,
    ScopeCreate,
    SourceRef,
    SpaceCreate,
)
from intelegent_data_catalog.registry.service import RegistryService
from tests.test_scope_graph import _link


def test_scope_graph_edges_skip_hierarchy_and_unknown_ids() -> None:
    """HAS_TABLE is not a scope edge. A bad catalog id is dropped."""
    orders: UUID = uuid4()
    invoices: UUID = uuid4()
    view: GraphView = GraphView(
        nodes=[
            GraphNode(id="scope", kind="scope", label="Ordering", catalog_id=str(uuid4())),
            GraphNode(id="orders", kind="table", label="orders", catalog_id=str(orders)),
            GraphNode(id="invoices", kind="table", label="invoices", catalog_id=str(invoices)),
            GraphNode(id="broken", kind="table", label="broken", catalog_id="not-a-uuid"),
        ],
        edges=[
            GraphEdge(source="scope", target="orders", kind="HAS_TABLE", style="hierarchy"),
            GraphEdge(
                source="orders",
                target="invoices",
                kind="JOINS_ON",
                style="relationship",
                reason="shared region",
            ),
            GraphEdge(
                source="orders",
                target="broken",
                kind="REFERENCES",
                style="relationship",
                reason="bad id",
            ),
        ],
        truncated=False,
    )
    edges = scope_graph_edges_from_view(view)
    assert len(edges) == 1
    assert edges[0].source_table_id == orders
    assert edges[0].target_table_id == invoices
    assert edges[0].kind == TableLinkKind.JOINS_ON
    assert edges[0].reason == "shared region"
    loaded = Neo4jScopeKnowledgeReader(_Graphs(view)).read(uuid4())
    assert loaded.present is True
    assert loaded.graph == edges


def test_merged_reader_dedupes_graph_edges_and_survives_a_neo4j_outage() -> None:
    """A graph edge that repeats a stored link is dropped. A graph failure keeps Postgres."""
    scope_id: UUID = uuid4()
    orders: UUID = uuid4()
    invoices: UUID = uuid4()
    link = _link(orders, invoices, uuid4(), uuid4(), TableLinkKind.JOINS_ON)
    repeated = scope_graph_edges_from_view(
        GraphView(
            nodes=[
                GraphNode(id="orders", kind="table", label="orders", catalog_id=str(orders)),
                GraphNode(id="invoices", kind="table", label="invoices", catalog_id=str(invoices)),
            ],
            edges=[
                GraphEdge(
                    source="orders",
                    target="invoices",
                    kind="JOINS_ON",
                    style="relationship",
                    reason="already stored",
                )
            ],
            truncated=False,
        )
    )
    postgres: _SliceReader = _SliceReader(
        ScopeKnowledgeSlice(
            summary="Ordering",
            links=[link],
            graph=[],
            present=True,
            origin="postgres",
        )
    )
    neo4j: _SliceReader = _SliceReader(
        ScopeKnowledgeSlice(
            summary=None,
            links=[],
            graph=repeated,
            present=True,
            origin="neo4j",
        )
    )
    merged: MergedScopeKnowledge = MergedScopeKnowledgeReader([postgres, neo4j]).read(scope_id)
    assert merged.source == "both"
    assert merged.summary == "Ordering"
    assert merged.links == [link]
    assert merged.graph == []
    failed: MergedScopeKnowledge = MergedScopeKnowledgeReader([postgres, _FailingReader()]).read(
        scope_id
    )
    assert failed.source == "postgres"
    assert failed.links == [link]


def test_provider_reports_scopes_without_context(tmp_path: Path) -> None:
    """A scope without a snapshot is listed. The previous space build is loaded."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        service: RegistryService = RegistryService(session)
        space_id: UUID = service.create_space(SpaceCreate(name="Commerce")).id
        ordering_id: UUID = service.create_scope(space_id, ScopeCreate(name="Ordering")).id
        billing_id: UUID = service.create_scope(space_id, ScopeCreate(name="Billing")).id
        source_id: UUID = service.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        _table(service, ordering_id, source_id, "orders")
        repository: RegistryRepository = RegistryRepository(session)
        space = service.get_space(space_id)
        scope = service.get_scope(ordering_id)
        ScopeKnowledgeContextStore(session).save(
            ScopeAnalysisContext(
                scope=ScopeSummary(
                    id=scope.id,
                    space_id=space_id,
                    name=scope.name,
                    slug=scope.slug,
                    description=scope.description,
                ),
                space=SpaceSummary(
                    id=space.id,
                    name=space.name,
                    slug=space.slug,
                    description=space.description,
                ),
                tables=[],
                existing_relationships=[],
                summary="Ordering covers checkout.",
            )
        )
        SpaceKnowledgeContextStore(session).save(
            SpaceAnalysisContext(
                space=SpaceSummary(
                    id=space.id,
                    name=space.name,
                    slug=space.slug,
                    description=space.description,
                ),
                scopes=[],
                existing_relationships=[],
                summary="Commerce covers sales.",
            )
        )
        loaded: SpaceCatalogContext = PostgresSpaceCatalogContextProvider(
            repository,
            MergedScopeKnowledgeReader(
                [PostgresScopeKnowledgeReader(ScopeKnowledgeContextStore(session))]
            ),
            SpaceKnowledgeContextStore(session),
        ).get_space_context(space_id)
        assert loaded.previous is not None
        assert loaded.previous.summary == "Commerce covers sales."
        assert billing_id in loaded.scopes_without_context
        assert ordering_id not in loaded.scopes_without_context
        assert loaded.scope_knowledge[0].scope_id == ordering_id
        assert loaded.scope_knowledge[0].source == "postgres"
        assert loaded.scope_knowledge[0].summary == "Ordering covers checkout."
    finally:
        session.close()
        database.dispose()


class _Graphs:
    """In-memory scope graph."""

    def __init__(self, view: GraphView) -> None:
        self._view: GraphView = view

    def read_owned(self, owner_property: str, owner_id: str) -> GraphView:
        del owner_property, owner_id
        return self._view


class _SliceReader:
    """Returns one prepared slice."""

    def __init__(self, item: ScopeKnowledgeSlice) -> None:
        self._item: ScopeKnowledgeSlice = item

    def read(self, scope_id: UUID) -> ScopeKnowledgeSlice:
        del scope_id
        return self._item


class _FailingReader:
    """Graph stand-in that is down."""

    def read(self, scope_id: UUID) -> ScopeKnowledgeSlice:
        del scope_id
        raise GraphUnavailableError("graph projection is unavailable")


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _table(service: RegistryService, scope_id: UUID, source_id: UUID, name: str) -> UUID:
    return service.create_table(
        scope_id,
        CatalogTableCreate(
            data_source_id=source_id,
            name=name,
            table_type=TableKind.TABLE,
            physical_catalog="polaris",
            physical_namespace="sales",
            physical_table=name,
            source_ref=SourceRef(catalog="polaris", namespace="sales", table=name),
        ),
    ).id
