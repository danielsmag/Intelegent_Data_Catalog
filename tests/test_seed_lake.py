"""Seeded Iceberg tables become registry rows the Spaces tree can list."""

import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.core.settings import AppSettings, load_settings
from intelegent_data_catalog.exceptions import TrinoUnavailableError
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import CapabilitySupport, TableKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.query.spark_bridge import SparkQueryPreview, spark_preview
from intelegent_data_catalog.registry.schemas import CatalogTableRead, SpaceRead
from intelegent_data_catalog.registry.seed_lake import SeedLakeRegistrar, project_seed_lake
from intelegent_data_catalog.sources.discovery import (
    DiscoveryBatch,
    DiscoveryRequest,
    SourceColumnRecord,
    SourceTableRecord,
)
from tests.registry_factory import OpenRegistry, open_registry

_LOG: logging.Logger = logging.getLogger("test-seed-lake")


class _FakeSource:
    """Discovery stand-in for the Polaris sales namespace."""

    def __init__(self, batch: DiscoveryBatch | None, failures: int = 0) -> None:
        self.batch: DiscoveryBatch | None = batch
        self.failures: int = failures
        self.calls: int = 0

    def discover(self, request: DiscoveryRequest) -> DiscoveryBatch:
        """Fail a few times, then return the prepared batch."""
        del request
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise TrinoUnavailableError("trino not ready")
        if self.batch is None:
            raise TrinoUnavailableError("no batch")
        return self.batch


def test_publish_lists_seed_tables_and_spark_sql(tmp_path: Path) -> None:
    """One projection creates Sales / sales and a Spark SELECT for each seed table."""
    database: CatalogDatabase = _database(tmp_path)
    settings: AppSettings = load_settings()
    source: _FakeSource = _FakeSource(_batch())
    registrar: SeedLakeRegistrar = SeedLakeRegistrar(database, settings, source, _LOG)
    try:
        assert registrar.publish() is True
        assert registrar.publish() is True
        assert source.calls == 2
        session: Session = database.session()
        try:
            service: OpenRegistry = open_registry(session)
            spaces: list[SpaceRead] = service.spaces.list_spaces()
            space: SpaceRead = next(item for item in spaces if item.slug == "sales")
            scope_id: UUID = next(
                item.id for item in service.scopes.list_scopes(space.id) if item.slug == "sales"
            )
            tables: list[CatalogTableRead] = service.tables.list_tables(scope_id)
            names: set[str] = {table.name for table in tables}
            assert names == {"customers", "orders", "customer_spend"}
            customers: CatalogTableRead = next(
                table for table in tables if table.name == "customers"
            )
            preview: SparkQueryPreview = spark_preview(
                service.bindings.list_asset_bindings(customers.id),
                100,
            )
            assert preview.sql == "SELECT * FROM catalog.sales.customers LIMIT 100"
            assert len(service.sources.list_data_sources()) == 1
            assert len(service.engines.list_engine_connections()) == 2
        finally:
            session.close()
    finally:
        database.dispose()


def test_project_retries_until_trino_lists_tables(tmp_path: Path) -> None:
    """A down Trino does not skip the later successful projection."""
    database: CatalogDatabase = _database(tmp_path)
    settings: AppSettings = load_settings()
    settings.trino.enabled = True
    pauses: list[float] = []
    source: _FakeSource = _FakeSource(_batch(), failures=2)
    try:
        ready: bool = project_seed_lake(
            database,
            settings,
            source,
            _LOG,
            attempts=3,
            pause_seconds=5.0,
            sleeper=pauses.append,
        )
        assert ready is True
        assert source.calls == 3
        assert pauses == [5.0]
    finally:
        database.dispose()


def test_project_skips_when_trino_is_disabled(tmp_path: Path) -> None:
    """Host settings leave Trino off, so startup does not invent a sales space."""
    database: CatalogDatabase = _database(tmp_path)
    settings: AppSettings = load_settings()
    source: _FakeSource = _FakeSource(_batch())
    try:
        ready: bool = project_seed_lake(database, settings, source, _LOG, attempts=1)
        assert ready is False
        assert source.calls == 0
    finally:
        database.dispose()


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _batch() -> DiscoveryBatch:
    names: tuple[str, ...] = ("customers", "orders", "customer_spend")
    records: list[SourceTableRecord] = []
    table_name: str
    for table_name in names:
        records.append(
            SourceTableRecord(
                catalog="polaris",
                namespace="sales",
                name=table_name,
                table_type=TableKind.TABLE,
                source_description="seed",
                columns=[
                    SourceColumnRecord(
                        name="id",
                        discovered_data_type="bigint",
                        nullable=False,
                        ordinal_position=0,
                    )
                ],
            )
        )
    return DiscoveryBatch(tables=records, relationship_capability=CapabilitySupport.UNKNOWN)
