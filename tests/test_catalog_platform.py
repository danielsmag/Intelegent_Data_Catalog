"""Discovery sync, Spark preview, graph identity, and search."""

from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.graph.document import GraphView
from intelegent_data_catalog.graph.projection import LineageLink
from intelegent_data_catalog.graph.service import GraphService
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    BindingRole,
    CapabilitySupport,
    DataSourceType,
    EngineType,
    RelationshipStatus,
    TableKind,
    TableStatus,
)
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import MarquezMappingRow
from intelegent_data_catalog.query.spark_bridge import NOT_CONFIGURED, spark_preview
from intelegent_data_catalog.registry.annotations import CatalogAnnotations, TagWrite
from intelegent_data_catalog.registry.schemas import (
    AssetBindingCreate,
    CatalogTableRead,
    ColumnRead,
    DataSourceCreate,
    EngineConnectionCreate,
    EngineRef,
    RelationshipCreate,
    RelationshipRead,
    SourceBindingCreate,
)
from intelegent_data_catalog.sources.discovery import (
    DiscoveryBatch,
    DiscoveryRequest,
    SourceColumnRecord,
    SourceTableRecord,
)
from intelegent_data_catalog.sources.sync import MetadataSyncService
from tests.registry_factory import OpenRegistry, open_registry


class _FakeSource:
    """Discovery stand-in that returns a prepared batch."""

    def __init__(self, batch: DiscoveryBatch) -> None:
        self.batch: DiscoveryBatch = batch
        self.calls: int = 0

    def discover(self, request: DiscoveryRequest) -> DiscoveryBatch:
        """Return the prepared batch."""
        del request
        self.calls += 1
        return self.batch


def test_sync_is_idempotent_and_keeps_user_text(tmp_path: Path) -> None:
    """A second scan does not duplicate rows or replace the user description."""
    database: CatalogDatabase
    session: Session
    database, session = _session(tmp_path)
    service: OpenRegistry = open_registry(session)
    try:
        binding_id: UUID
        source_id: UUID
        binding_id, source_id = _binding(service)
        sync: MetadataSyncService = MetadataSyncService(
            session,
            _FakeSource(_batch("lakehouse", ["orders", "customers"])),
        )
        first = sync.run(binding_id)
        assert first.status.value == "SUCCEEDED"
        assert first.counts.tables_seen == 2
        again = sync.run(binding_id)
        assert again.status.value == "SUCCEEDED"
        tables: list[CatalogTableRead] = _tables(service)
        assert len(tables) == 2
        orders: CatalogTableRead = next(table for table in tables if table.name == "orders")
        from intelegent_data_catalog.registry.schemas import CatalogTableUpdate

        service.tables.update_table(orders.id, CatalogTableUpdate(description="User orders"))
        sync.run(binding_id)
        kept: CatalogTableRead = service.tables.get_table(orders.id)
        assert kept.description == "User orders"
        assert kept.source_key == "lakehouse.sales.orders"
        other: UUID = service.sources.create_data_source(
            DataSourceCreate(name="App", type=DataSourceType.POSTGRES)
        ).id
        from intelegent_data_catalog.registry.schemas import CatalogTableCreate, SourceRef

        service.tables.create_table(
            CatalogTableCreate(
                data_source_id=other,
                name="orders",
                table_type=TableKind.TABLE,
                physical_catalog="lakehouse",
                physical_namespace="sales",
                physical_table="orders",
                source_ref=SourceRef(catalog="lakehouse", namespace="sales", table="orders"),
            ),
        )
        assert source_id != other
    finally:
        session.close()
        database.dispose()


def test_partial_scan_does_not_mark_tables_missing(tmp_path: Path) -> None:
    """A truncated scan leaves assets that were not visited."""
    database: CatalogDatabase
    session: Session
    database, session = _session(tmp_path)
    service: OpenRegistry = open_registry(session)
    try:
        binding_id: UUID
        binding_id, _source_id = _binding(service)
        MetadataSyncService(
            session,
            _FakeSource(_batch("lakehouse", ["orders", "customers"])),
        ).run(binding_id)
        truncated: DiscoveryBatch = _batch("lakehouse", ["orders"]).model_copy(
            update={"truncated": True}
        )
        result = MetadataSyncService(session, _FakeSource(truncated)).run(binding_id)
        assert result.status.value == "PARTIAL"
        customers: CatalogTableRead = next(
            table for table in _tables(service) if table.name == "customers"
        )
        assert customers.status is TableStatus.ACTIVE
        MetadataSyncService(session, _FakeSource(_batch("lakehouse", ["orders"]))).run(binding_id)
        customers = next(table for table in _tables(service) if table.name == "customers")
        assert customers.status is TableStatus.MISSING
    finally:
        session.close()
        database.dispose()


def test_removed_column_disables_relationship_and_unknown_fk_keeps_manual(tmp_path: Path) -> None:
    """Column removal disables mappings. Unknown foreign keys are not treated as none."""
    database: CatalogDatabase
    session: Session
    database, session = _session(tmp_path)
    service: OpenRegistry = open_registry(session)
    try:
        binding_id: UUID
        binding_id, _source_id = _binding(service)
        MetadataSyncService(
            session,
            _FakeSource(_batch("lakehouse", ["orders", "customers"])),
        ).run(binding_id)
        tables: list[CatalogTableRead] = _tables(service)
        orders: CatalogTableRead = next(table for table in tables if table.name == "orders")
        customers: CatalogTableRead = next(table for table in tables if table.name == "customers")
        order_columns: list[ColumnRead] = service.tables.list_columns(orders.id)
        order_column: ColumnRead = next(
            column for column in order_columns if column.name == "customer_id"
        )
        customer_columns: list[ColumnRead] = service.tables.list_columns(customers.id)
        customer_column = next(
            column for column in customer_columns if column.name == "customer_id"
        )
        relationship = service.relationships.create_relationship(
            RelationshipCreate(
                source_table_id=orders.id,
                target_table_id=customers.id,
                column_mappings=[
                    {
                        "source_column_id": order_column.id,
                        "target_column_id": customer_column.id,
                    }
                ],
            )
        )
        unknown: DiscoveryBatch = DiscoveryBatch(
            tables=_batch("lakehouse", ["orders", "customers"]).tables,
            relationship_capability=CapabilitySupport.UNKNOWN,
        )
        MetadataSyncService(session, _FakeSource(unknown)).run(binding_id)
        stored: RelationshipRead = service.relationships.get_relationship(relationship.id)
        assert stored.status is RelationshipStatus.UNVERIFIED
        MetadataSyncService(
            session,
            _FakeSource(_batch("lakehouse", ["orders", "customers"], columns=["order_id"])),
        ).run(binding_id)
        disabled: RelationshipRead = service.relationships.get_relationship(relationship.id)
        assert disabled.status is RelationshipStatus.DISABLED
        names: list[str] = [column.name for column in service.tables.list_columns(orders.id)]
        assert "customer_id" in names
    finally:
        session.close()
        database.dispose()


def test_spark_preview_uses_the_selected_query_binding(tmp_path: Path) -> None:
    """No Spark binding is an explicit unavailable state. The SQL uses the Spark address."""
    database: CatalogDatabase
    session: Session
    database, session = _session(tmp_path)
    service: OpenRegistry = open_registry(session)
    try:
        binding_id: UUID
        source_id: UUID
        binding_id, source_id = _binding(service)
        MetadataSyncService(session, _FakeSource(_batch("lakehouse", ["orders"]))).run(binding_id)
        orders: CatalogTableRead = next(
            table for table in _tables(service) if table.name == "orders"
        )
        missing = spark_preview(service.bindings.list_asset_bindings(orders.id), 100)
        assert missing.available is False
        assert missing.message == NOT_CONFIGURED
        spark_id: UUID = service.engines.create_engine_connection(
            EngineConnectionCreate(
                name="Spark",
                engine_type=EngineType.SPARK_SQL,
                endpoint="http://spark:8091",
            )
        ).id
        query_binding: UUID = service.bindings.create_source_binding(
            SourceBindingCreate(
                data_source_id=source_id,
                engine_connection_id=spark_id,
                binding_key="catalog",
            )
        ).id
        service.bindings.create_asset_binding(
            orders.id,
            AssetBindingCreate(
                source_engine_binding_id=query_binding,
                engine_ref=EngineRef(catalog="catalog", namespace="sales", table="orders"),
                engine_ref_key="catalog.sales.orders",
                role=BindingRole.QUERY,
                is_selected=True,
            ),
        )
        preview = spark_preview(service.bindings.list_asset_bindings(orders.id), 100)
        assert preview.sql == "SELECT * FROM catalog.sales.orders LIMIT 100"
        assert preview.engine_ref_key == "catalog.sales.orders"
        assert "lakehouse" not in (preview.sql or "")
    finally:
        session.close()
        database.dispose()


def test_graph_keeps_catalog_ids_and_unresolved_lineage(tmp_path: Path) -> None:
    """Lineage keeps PostgreSQL ids. An unmapped Marquez dataset stays unresolved."""
    database: CatalogDatabase
    session: Session
    database, session = _session(tmp_path)
    service: OpenRegistry = open_registry(session)
    try:
        binding_id: UUID
        binding_id, _source_id = _binding(service)
        MetadataSyncService(session, _FakeSource(_batch("lakehouse", ["orders"]))).run(binding_id)
        orders: CatalogTableRead = next(
            table for table in _tables(service) if table.name == "orders"
        )
        session.add(
            MarquezMappingRow(
                id=UUID(int=7),
                catalog_table_id=orders.id,
                marquez_namespace="production",
                marquez_dataset_name="orders",
                created_at=orders.created_at,
                updated_at=orders.updated_at,
            )
        )
        session.commit()
        view: GraphView = GraphService(session, _Lineage()).lineage(orders.id, 1, "both")
        catalog_ids: set[str | None] = {node.catalog_id for node in view.nodes}
        assert str(orders.id) in catalog_ids
        assert any(node.resolved is False and node.label == "raw_orders" for node in view.nodes)
    finally:
        session.close()
        database.dispose()


def test_search_finds_columns_and_tags(tmp_path: Path) -> None:
    """PostgreSQL search returns columns and tags."""
    database: CatalogDatabase
    session: Session
    database, session = _session(tmp_path)
    service: OpenRegistry = open_registry(session)
    annotations: CatalogAnnotations = CatalogAnnotations(session)
    try:
        binding_id: UUID
        binding_id, _source_id = _binding(service)
        MetadataSyncService(session, _FakeSource(_batch("lakehouse", ["orders"]))).run(binding_id)
        orders: CatalogTableRead = next(
            table for table in _tables(service) if table.name == "orders"
        )
        tag = annotations.create_tag(TagWrite(name="PII", description="personal data"))
        annotations.attach_tag(orders.id, tag.id)
        hits = annotations.search("customer")
        assert any(hit.kind == "column" and hit.name == "customer_id" for hit in hits.hits)
        tagged = annotations.search("PII")
        assert any(hit.kind == "tag" for hit in tagged.hits)
    finally:
        session.close()
        database.dispose()


class _Lineage:
    """One mapped output and one unmapped input."""

    def read_links(self) -> list[LineageLink]:
        """Return a single job edge."""
        return [
            LineageLink(
                input_namespace="production",
                input_name="raw_orders",
                job_namespace="production",
                job_name="orders_job",
                output_namespace="production",
                output_name="orders",
            )
        ]


def _session(tmp_path: Path) -> tuple[CatalogDatabase, Session]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    return database, database.session()


def _binding(service: OpenRegistry) -> tuple[UUID, UUID]:
    source_id: UUID = service.sources.create_data_source(
        DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
    ).id
    engine_id: UUID = service.engines.create_engine_connection(
        EngineConnectionCreate(
            name="Trino",
            engine_type=EngineType.TRINO,
            endpoint="http://trino:8080",
        )
    ).id
    binding_id: UUID = service.bindings.create_source_binding(
        SourceBindingCreate(
            data_source_id=source_id,
            engine_connection_id=engine_id,
            binding_key="lakehouse",
        )
    ).id
    return binding_id, source_id


def _batch(
    catalog: str,
    tables: list[str],
    columns: list[str] | None = None,
) -> DiscoveryBatch:
    column_names: list[str] = columns or ["order_id", "customer_id"]
    records: list[SourceTableRecord] = []
    table_name: str
    for table_name in tables:
        records.append(
            SourceTableRecord(
                catalog=catalog,
                namespace="sales",
                name=table_name,
                table_type=TableKind.TABLE,
                source_description="imported",
                columns=[
                    SourceColumnRecord(
                        name=column_name,
                        discovered_data_type="bigint",
                        nullable=False,
                        ordinal_position=index,
                    )
                    for index, column_name in enumerate(column_names)
                ],
            )
        )
    return DiscoveryBatch(
        tables=records,
        relationship_capability=CapabilitySupport.UNKNOWN,
    )


def _tables(service: OpenRegistry) -> list[CatalogTableRead]:
    return service.tables.list_tables()
