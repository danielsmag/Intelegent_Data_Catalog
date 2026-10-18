"""Persistence rules for the application catalog."""

from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import (
    ConflictError,
    EntityNotFoundError,
    ValidationAppError,
)
from intelegent_data_catalog.persistence import migrate as migrate_module
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    BindingRole,
    BindingState,
    ContextOrigin,
    DataSourceType,
    EngineType,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    TableKind,
    TableStatus,
)
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import AssetEngineBindingRow, SpaceRow
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    AssetBindingCreate,
    CatalogTableCreate,
    CatalogTableRead,
    CatalogTableUpdate,
    ColumnCreate,
    ColumnMappingWrite,
    ColumnObservation,
    ColumnUpdate,
    DataSourceCreate,
    EngineConnectionCreate,
    EngineRef,
    RelationshipCreate,
    RelationshipRead,
    ScopeCreate,
    ScopeUpdate,
    SourceBindingCreate,
    SourceBindingRead,
    SourceRef,
    SourceSnapshot,
    SpaceCreate,
    SpaceUpdate,
)
from tests.registry_factory import OpenRegistry, open_registry

_CORE_TABLES: frozenset[str] = frozenset(
    {
        "spaces",
        "scopes",
        "data_sources",
        "catalog_tables",
        "columns",
        "relationships",
        "relationship_column_mappings",
        "engine_connections",
        "source_engine_bindings",
        "asset_engine_bindings",
        "metadata_sync_runs",
        "scope_knowledge_contexts",
        "space_knowledge_contexts",
    }
)


def test_migration_creates_core_tables(tmp_path: Path) -> None:
    """The baseline migration creates every canonical catalog table."""
    database: CatalogDatabase = _database(tmp_path)
    engine: Engine = _engine(database)
    try:
        names: set[str] = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        database.dispose()
    assert names >= _CORE_TABLES


def test_scope_migration_allows_llm_origin_after_upgrade(tmp_path: Path) -> None:
    """Revision 0004 adds the scope snapshot and the LLM origin on an older database."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    config: Config = Config()
    script: Path = Path(migrate_module.__file__).resolve().parent / "migrations"
    config.set_main_option("script_location", str(script))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "20261007_0003")
    command.upgrade(config, "head")
    database: CatalogDatabase = CatalogDatabase(url)
    session: Session = database.session()
    service: OpenRegistry = open_registry(session)
    engine: Engine = _engine(database)
    try:
        names: set[str] = set(inspect(engine).get_table_names())
        assert "scope_knowledge_contexts" in names
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
        scope_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        orders: CatalogTableRead = _table(
            service, scope_id, source_id, "polaris", "sales", "orders"
        )
        customers: CatalogTableRead = _table(
            service, scope_id, source_id, "polaris", "sales", "customers"
        )
        source_column: UUID = service.tables.create_column(
            orders.id,
            ColumnCreate(name="customer_id", data_type="bigint", ordinal_position=0),
        ).id
        target_column: UUID = service.tables.create_column(
            customers.id,
            ColumnCreate(name="id", data_type="bigint", ordinal_position=0, is_primary_key=True),
        ).id
        created: RelationshipRead = service.relationships.create_relationship(
            RelationshipCreate(
                source_table_id=orders.id,
                target_table_id=customers.id,
                relationship_type=RelationshipType.FOREIGN_KEY,
                origin=RelationshipOrigin.LLM,
                status=RelationshipStatus.UNVERIFIED,
                description="customer_id points at the customer key",
                column_mappings=[
                    ColumnMappingWrite(
                        source_column_id=source_column,
                        target_column_id=target_column,
                        position=0,
                    )
                ],
            )
        )
        assert created.origin == RelationshipOrigin.LLM
    finally:
        session.close()
        engine.dispose()
        database.dispose()


def test_space_migration_adds_context_and_the_snapshot_table(tmp_path: Path) -> None:
    """Revision 0006 adds space context columns and the snapshot on an older database."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    config: Config = Config()
    script: Path = Path(migrate_module.__file__).resolve().parent / "migrations"
    config.set_main_option("script_location", str(script))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "20261010_0005")
    database: CatalogDatabase = CatalogDatabase(url)
    engine: Engine = _engine(database)
    try:
        names: set[str] = set(inspect(engine).get_table_names())
        columns: set[str] = {column["name"] for column in inspect(engine).get_columns("spaces")}
        assert "space_knowledge_contexts" not in names
        assert "context" not in columns
    finally:
        engine.dispose()
        database.dispose()
    command.upgrade(config, "head")
    upgraded: CatalogDatabase = CatalogDatabase(url)
    upgraded_engine: Engine = _engine(upgraded)
    try:
        names = set(inspect(upgraded_engine).get_table_names())
        columns = {column["name"] for column in inspect(upgraded_engine).get_columns("spaces")}
        assert "space_knowledge_contexts" in names
        assert "context" in columns
        assert "context_origin" in columns
    finally:
        upgraded_engine.dispose()
        upgraded.dispose()


def test_airflow_knowledge_migration_adds_attachments_and_widens_checks(tmp_path: Path) -> None:
    """Revision 0012 adds attachment storage and allows LLM DAG links and airflow jobs."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    config: Config = Config()
    script: Path = Path(migrate_module.__file__).resolve().parent / "migrations"
    config.set_main_option("script_location", str(script))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "20261013_0011")
    database: CatalogDatabase = CatalogDatabase(url)
    engine: Engine = _engine(database)
    try:
        names: set[str] = set(inspect(engine).get_table_names())
        columns: set[str] = {
            column["name"] for column in inspect(engine).get_columns("airflow_instances")
        }
        assert "airflow_attachments" not in names
        assert "airflow_instance_knowledge_contexts" not in names
        assert "context" not in columns
    finally:
        engine.dispose()
        database.dispose()
    command.upgrade(config, "head")
    upgraded: CatalogDatabase = CatalogDatabase(url)
    upgraded_engine: Engine = _engine(upgraded)
    try:
        names = set(inspect(upgraded_engine).get_table_names())
        columns = {
            column["name"] for column in inspect(upgraded_engine).get_columns("airflow_instances")
        }
        assert "airflow_attachments" in names
        assert "airflow_instance_knowledge_contexts" in names
        assert "context" in columns
        origin: str = _check_sql(
            upgraded_engine,
            "airflow_dag_tables",
            "ck_airflow_dag_tables_origin",
        )
        kind: str = _check_sql(upgraded_engine, "catalog_jobs", "ck_catalog_jobs_kind")
        assert "LLM" in origin
        assert "airflow" in kind
    finally:
        upgraded_engine.dispose()
        upgraded.dispose()


def _check_sql(engine: Engine, table: str, name: str) -> str:
    check: dict[str, object]
    for check in inspect(engine).get_check_constraints(table):
        if check.get("name") != name:
            continue
        sqltext: object = check.get("sqltext")
        assert isinstance(sqltext, str)
        return sqltext
    raise AssertionError(f"missing check {name}")


def test_space_delete_is_restricted_while_a_scope_exists(tmp_path: Path) -> None:
    """A space row cannot disappear while a scope still points at it."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    service: OpenRegistry = open_registry(session)
    try:
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
        service.scopes.create_scope(space_id, ScopeCreate(name="Ordering"))
        with pytest.raises(IntegrityError):
            session.execute(delete(SpaceRow).where(SpaceRow.id == space_id))
            session.commit()
    finally:
        session.close()
        database.dispose()


def test_scope_delete_clears_default_on_source_bindings(tmp_path: Path) -> None:
    """Dropping a scope unsets it as the default. Other defaults stay."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    service: OpenRegistry = open_registry(session)
    try:
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
        dropped_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id
        kept_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Billing")).id
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
        dropped_binding: SourceBindingRead = service.bindings.create_source_binding(
            SourceBindingCreate(
                data_source_id=source_id,
                engine_connection_id=engine_id,
                binding_key="lake",
                default_scope_id=dropped_id,
            )
        )
        kept_binding: SourceBindingRead = service.bindings.create_source_binding(
            SourceBindingCreate(
                data_source_id=source_id,
                engine_connection_id=engine_id,
                binding_key="billing",
                default_scope_id=kept_id,
            )
        )
        service.scopes.delete_scope(dropped_id)
        cleared: SourceBindingRead = service.bindings.get_source_binding(dropped_binding.id)
        assert cleared.default_scope_id is None
        untouched: SourceBindingRead = service.bindings.get_source_binding(kept_binding.id)
        assert untouched.default_scope_id == kept_id
        with pytest.raises(EntityNotFoundError, match="scope"):
            service.scopes.get_scope(dropped_id)
    finally:
        session.close()
        database.dispose()


def test_source_key_is_case_sensitive_per_data_source(tmp_path: Path) -> None:
    """Identical names stay distinct across sources, and key case is preserved."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    service: OpenRegistry = open_registry(session)
    try:
        scope_id: UUID = _scope(service)
        iceberg_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        postgres_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="App", type=DataSourceType.POSTGRES)
        ).id
        first: str = _table(
            service, scope_id, iceberg_id, "Lakehouse", "Sales", "Orders"
        ).source_key
        second: str = _table(
            service, scope_id, iceberg_id, "lakehouse", "sales", "orders"
        ).source_key
        third: str = _table(
            service, scope_id, postgres_id, "Lakehouse", "Sales", "Orders"
        ).source_key
        assert first == "Lakehouse.Sales.Orders"
        assert second == "lakehouse.sales.orders"
        assert third == first
        with pytest.raises(ConflictError, match="source_key already exists"):
            _table(service, scope_id, iceberg_id, "Lakehouse", "Sales", "Orders")
    finally:
        session.close()
        database.dispose()


def test_snapshot_preserves_user_description_scope_and_deprecated(tmp_path: Path) -> None:
    """Imported fields change. User description, scope, and DEPRECATED do not."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    service: OpenRegistry = open_registry(session)
    try:
        scope_id: UUID = _scope(service)
        other_scope: UUID = service.scopes.create_scope(
            service.spaces.list_spaces()[0].id,
            ScopeCreate(name="Payments"),
        ).id
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        table = service.tables.create_table(
            scope_id,
            _table_body(source_id, "Lakehouse", "Sales", "Orders", description="User orders"),
        )
        service.tables.create_column(
            table.id,
            ColumnCreate(name="customer_id", ordinal_position=0, description="User column"),
        )
        moved = service.tables.update_table(
            table.id,
            _status_patch(TableStatus.DEPRECATED, other_scope),
        )
        assert moved.scope_id == other_scope
        observed = service.tables.apply_source_snapshot(
            table.id,
            SourceSnapshot(
                source_description="Imported orders",
                status=TableStatus.ACTIVE,
                row_count=10,
                columns=[
                    ColumnObservation(
                        name="customer_id",
                        discovered_data_type="bigint",
                        source_description="Imported column",
                        nullable=False,
                    )
                ],
            ),
        )
        assert observed.description == "User orders"
        assert observed.source_description == "Imported orders"
        assert observed.status is TableStatus.DEPRECATED
        assert observed.scope_id == other_scope
        assert observed.row_count == 10
        assert observed.last_seen_at is not None
        columns = service.tables.list_columns(table.id)
        assert len(columns) == 1
        assert columns[0].description == "User column"
        assert columns[0].source_description == "Imported column"
        assert columns[0].discovered_data_type == "bigint"
        assert columns[0].data_type is None
        assert columns[0].nullable is False
        unknown = service.tables.apply_source_snapshot(table.id, SourceSnapshot())
        assert unknown.description == "User orders"
        still_unknown = service.tables.list_columns(table.id)[0]
        assert still_unknown.is_primary_key is None
        with pytest.raises(ValidationAppError, match="not on the table"):
            service.tables.apply_source_snapshot(
                table.id,
                SourceSnapshot(columns=[ColumnObservation(name="missing")]),
            )
    finally:
        session.close()
        database.dispose()


def test_manual_context_records_user_origin_and_clearing_resets_it(tmp_path: Path) -> None:
    """A manual context edit is USER. Blank text clears the text and the origin."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    service: OpenRegistry = open_registry(session)
    try:
        scope_id: UUID = _scope(service)
        written = service.scopes.update_scope(scope_id, ScopeUpdate(context="  Checkout scope.  "))
        assert written.context == "Checkout scope."
        assert written.context_origin is ContextOrigin.USER
        space_id: UUID = service.spaces.create_space(SpaceCreate(name="Retail")).id
        space_written = service.spaces.update_space(
            space_id,
            SpaceUpdate(context="  Retail space.  "),
        )
        assert space_written.context == "Retail space."
        assert space_written.context_origin is ContextOrigin.USER
        cleared = service.scopes.update_scope(scope_id, ScopeUpdate(context="   "))
        assert cleared.context is None
        assert cleared.context_origin is None
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        table: CatalogTableRead = _table(service, scope_id, source_id, "polaris", "sales", "orders")
        table_written = service.tables.update_table(
            table.id,
            CatalogTableUpdate(context="Order facts."),
        )
        assert table_written.context == "Order facts."
        assert table_written.context_origin is ContextOrigin.USER
        column = service.tables.create_column(
            table.id,
            ColumnCreate(name="customer_id", ordinal_position=0),
        )
        column_written = service.tables.update_column(column.id, ColumnUpdate(context="Buyer id."))
        assert column_written.context == "Buyer id."
        assert column_written.context_origin is ContextOrigin.USER
        column_cleared = service.tables.update_column(column.id, ColumnUpdate(context=None))
        assert column_cleared.context is None
        assert column_cleared.context_origin is None
    finally:
        session.close()
        database.dispose()


def test_selected_query_binding_is_unique_and_role_matches_engine(tmp_path: Path) -> None:
    """The database rejects a second selected query binding and a mismatched role."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    service: OpenRegistry = open_registry(session)
    repo: RegistryRepository = RegistryRepository(session)
    try:
        scope_id: UUID = _scope(service)
        source_id: UUID = service.sources.create_data_source(
            DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
        ).id
        table_id: UUID = _table(service, scope_id, source_id, "polaris", "sales", "orders").id
        spark_id: UUID = service.engines.create_engine_connection(
            EngineConnectionCreate(
                name="Spark",
                engine_type=EngineType.SPARK_SQL,
                endpoint="http://spark:8091",
            )
        ).id
        binding_id: UUID = service.bindings.create_source_binding(
            SourceBindingCreate(
                data_source_id=source_id,
                engine_connection_id=spark_id,
                binding_key="polaris",
            )
        ).id
        selected: UUID = service.bindings.create_asset_binding(
            table_id,
            _asset_body(binding_id, "polaris.sales.orders", BindingRole.QUERY, True),
        ).id
        with pytest.raises(ConflictError, match="selected QUERY binding"):
            service.bindings.create_asset_binding(
                table_id,
                _asset_body(binding_id, "polaris.sales.orders_v2", BindingRole.QUERY, True),
            )
        with pytest.raises(ConflictError):
            repo.add_asset_binding(
                AssetEngineBindingRow(
                    id=UUID(int=9),
                    catalog_table_id=table_id,
                    data_source_id=source_id,
                    source_engine_binding_id=binding_id,
                    engine_type=EngineType.SPARK_SQL.value,
                    engine_ref={"catalog": "polaris", "namespace": "sales", "table": "orders_v3"},
                    engine_ref_key="polaris.sales.orders_v3",
                    role=BindingRole.QUERY.value,
                    state=BindingState.UNVALIDATED.value,
                    is_selected=True,
                    last_validated_at=None,
                    created_at=service.tables.get_table(table_id).created_at,
                    updated_at=service.tables.get_table(table_id).updated_at,
                )
            )
        with pytest.raises(ConflictError):
            repo.add_asset_binding(
                AssetEngineBindingRow(
                    id=UUID(int=10),
                    catalog_table_id=table_id,
                    data_source_id=source_id,
                    source_engine_binding_id=binding_id,
                    engine_type=EngineType.SPARK_SQL.value,
                    engine_ref={"catalog": "polaris", "namespace": "sales", "table": "orders"},
                    engine_ref_key="polaris.sales.discovery",
                    role=BindingRole.DISCOVERY.value,
                    state=BindingState.UNVALIDATED.value,
                    is_selected=False,
                    last_validated_at=None,
                    created_at=service.tables.get_table(table_id).created_at,
                    updated_at=service.tables.get_table(table_id).updated_at,
                )
            )
        assert selected is not None
    finally:
        session.rollback()
        session.close()
        database.dispose()


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _engine(database: CatalogDatabase) -> Engine:
    from sqlalchemy import create_engine

    return create_engine(database.url)


def _scope(service: OpenRegistry) -> UUID:
    space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
    return service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id


def _table(
    service: OpenRegistry,
    scope_id: UUID,
    data_source_id: UUID,
    catalog: str,
    namespace: str,
    table: str,
) -> CatalogTableRead:
    body: CatalogTableCreate = _table_body(data_source_id, catalog, namespace, table)
    return service.tables.create_table(scope_id, body)


def _table_body(
    data_source_id: UUID,
    catalog: str,
    namespace: str,
    table: str,
    description: str | None = None,
) -> CatalogTableCreate:
    return CatalogTableCreate(
        data_source_id=data_source_id,
        name=table,
        description=description,
        source_description="imported",
        table_type=TableKind.TABLE,
        physical_catalog=catalog,
        physical_namespace=namespace,
        physical_table=table,
        source_ref=SourceRef(catalog=catalog, namespace=namespace, table=table),
        table_format="ICEBERG",
    )


def _status_patch(status: TableStatus, scope_id: UUID) -> CatalogTableUpdate:
    return CatalogTableUpdate(status=status, scope_id=scope_id)


def _asset_body(
    source_binding_id: UUID,
    engine_ref_key: str,
    role: BindingRole,
    is_selected: bool,
) -> AssetBindingCreate:
    return AssetBindingCreate(
        source_engine_binding_id=source_binding_id,
        engine_ref=EngineRef(catalog="polaris", namespace="sales", table="orders"),
        engine_ref_key=engine_ref_key,
        role=role,
        is_selected=is_selected,
    )
