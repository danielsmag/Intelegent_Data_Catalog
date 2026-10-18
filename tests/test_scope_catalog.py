"""Merged table knowledge and the scope catalog provider."""

from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import GraphUnavailableError
from intelegent_data_catalog.graph.document import GraphEdge, GraphNode, GraphView
from intelegent_data_catalog.knowledge.context.catalog import PostgresCatalogContextProvider
from intelegent_data_catalog.knowledge.context.scope_catalog import (
    MergedTableKnowledge,
    MergedTableKnowledgeReader,
    Neo4jTableKnowledgeReader,
    PostgresScopeCatalogContextProvider,
    PostgresTableKnowledgeReader,
    TableKnowledgeSlice,
    column_relations_from_graph,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnRelation,
    ColumnRelationKind,
    ScopeAnalysisContext,
    ScopeCatalogContext,
    TableAnalysisContext,
    TableCatalogContext,
)
from intelegent_data_catalog.knowledge.store import (
    KnowledgeContextStore,
    ScopeKnowledgeContextStore,
)
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import DataSourceType, TableKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    CatalogTableCreate,
    ColumnCreate,
    DataSourceCreate,
    ScopeCreate,
    SourceRef,
    SpaceCreate,
)
from tests.registry_factory import OpenRegistry, open_registry


def test_graph_reader_skips_hierarchy_edges() -> None:
    """HAS_COLUMN is not a column relation. A real edge becomes one."""
    source: UUID = uuid4()
    target: UUID = uuid4()
    table: UUID = uuid4()
    view: GraphView = GraphView(
        nodes=[
            GraphNode(id="table", kind="table", label="orders", catalog_id=str(table)),
            GraphNode(id="source", kind="column", label="customer_id", catalog_id=str(source)),
            GraphNode(id="target", kind="column", label="id", catalog_id=str(target)),
        ],
        edges=[
            GraphEdge(
                source="table",
                target="source",
                kind="HAS_COLUMN",
                style="hierarchy",
            ),
            GraphEdge(
                source="source",
                target="target",
                kind="REFERENCES",
                style="relationship",
                reason="customer key",
            ),
        ],
        truncated=False,
    )
    relations: list[ColumnRelation] = column_relations_from_graph(view)
    assert len(relations) == 1
    assert relations[0].source_column_id == source
    assert relations[0].target_column_id == target
    assert relations[0].kind == ColumnRelationKind.REFERENCES
    assert relations[0].reason == "customer key"
    reader: Neo4jTableKnowledgeReader = Neo4jTableKnowledgeReader(_Graphs(view))
    loaded: TableKnowledgeSlice = reader.read(table)
    assert loaded.present is True
    assert loaded.relations == relations


def test_merged_reader_dedupes_and_survives_a_neo4j_outage() -> None:
    """Postgres and Neo4j relations combine. A graph failure keeps the Postgres rows."""
    table_id: UUID = uuid4()
    source: UUID = uuid4()
    target: UUID = uuid4()
    shared: ColumnRelation = _relation(source, target)
    extra: ColumnRelation = _relation(source, uuid4(), ColumnRelationKind.SAME_MEANING)
    postgres: _SliceReader = _SliceReader(
        TableKnowledgeSlice(relations=[shared], present=True, origin="postgres")
    )
    neo4j: _SliceReader = _SliceReader(
        TableKnowledgeSlice(relations=[shared, extra], present=True, origin="neo4j")
    )
    merged: MergedTableKnowledge = MergedTableKnowledgeReader([postgres, neo4j]).read(table_id)
    assert merged.source == "both"
    assert len(merged.relations) == 2
    failed: MergedTableKnowledge = MergedTableKnowledgeReader(
        [postgres, _FailingReader()]
    ).read(table_id)
    assert failed.source == "postgres"
    assert failed.relations == [shared]


def test_provider_reports_missing_context_and_previous_findings(tmp_path: Path) -> None:
    """A table without a snapshot is listed. The previous scope build is loaded."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        service: OpenRegistry = open_registry(session)
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
        scope_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        orders_id: UUID = _table(service, scope_id, source_id, "orders")
        customers_id: UUID = _table(service, scope_id, source_id, "customers")
        order_column: UUID = service.tables.create_column(
            orders_id,
            ColumnCreate(name="customer_id", data_type="bigint", ordinal_position=0),
        ).id
        customer_column: UUID = service.tables.create_column(
            customers_id,
            ColumnCreate(name="id", data_type="bigint", ordinal_position=0, is_primary_key=True),
        ).id
        repository: RegistryRepository = RegistryRepository(session)
        catalog: TableAnalysisContext = _analysis(
            PostgresCatalogContextProvider(repository).get_table_context(orders_id),
            _relation(order_column, customer_column),
        )
        KnowledgeContextStore(session).save(catalog)
        saved: ScopeAnalysisContext = ScopeAnalysisContext(
            scope=catalog.table.scope,
            space=catalog.table.space,
            tables=[],
            existing_relationships=[],
            table_links=[],
            summary="Sales scope",
            tables_without_context=[],
            previous_links_kept=0,
        )
        ScopeKnowledgeContextStore(session).save(saved)
        loaded: ScopeCatalogContext = PostgresScopeCatalogContextProvider(
            repository,
            MergedTableKnowledgeReader([PostgresTableKnowledgeReader(KnowledgeContextStore(session))]),
            ScopeKnowledgeContextStore(session),
        ).get_scope_context(scope_id)
        assert loaded.previous is not None
        assert loaded.previous.summary == "Sales scope"
        assert loaded.previous.built_at is not None
        assert customers_id in loaded.tables_without_context
        assert orders_id not in loaded.tables_without_context
        assert loaded.table_knowledge[0].table_id == orders_id
        assert loaded.table_knowledge[0].source == "postgres"
        assert loaded.table_knowledge[0].column_relations[0].source_column_id == order_column
    finally:
        session.close()
        database.dispose()


class _Graphs:
    """In-memory knowledge graph."""

    def __init__(self, view: GraphView) -> None:
        self._view: GraphView = view

    def read_knowledge(self, owner_table_id: str) -> GraphView:
        del owner_table_id
        return self._view


class _SliceReader:
    """Returns one prepared slice."""

    def __init__(self, item: TableKnowledgeSlice) -> None:
        self._item: TableKnowledgeSlice = item

    def read(self, table_id: UUID) -> TableKnowledgeSlice:
        del table_id
        return self._item


class _FailingReader:
    """Graph stand-in that is down."""

    def read(self, table_id: UUID) -> TableKnowledgeSlice:
        del table_id
        raise GraphUnavailableError("graph projection is unavailable")


def _relation(
    source: UUID,
    target: UUID,
    kind: ColumnRelationKind = ColumnRelationKind.REFERENCES,
) -> ColumnRelation:
    return ColumnRelation(
        source_column_id=source,
        target_column_id=target,
        kind=kind,
        reason="customer_id points at the customer key",
    )


def _analysis(context: TableCatalogContext, relation: ColumnRelation) -> TableAnalysisContext:
    return TableAnalysisContext(
        table=context.table,
        columns=context.columns,
        data_profile=None,
        existing_relationships=context.relationships,
        graph_neighbors=[],
        lineage=None,
        column_relations=[relation],
    )


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _table(service: OpenRegistry, scope_id: UUID, source_id: UUID, name: str) -> UUID:
    return service.tables.create_table(
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
