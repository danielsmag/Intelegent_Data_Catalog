"""Project the seeded Polaris sales tables into the catalog registry.

The Spark seed writes Iceberg files and Polaris metadata. The Spaces tree
reads PostgreSQL, so those tables stay invisible until this registrar syncs them.
"""

import logging
import time
from collections.abc import Callable
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session

from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    BindingRole,
    BindingState,
    DataSourceType,
    EngineType,
    SyncRunStatus,
)
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    AssetBindingCreate,
    AssetBindingRead,
    CatalogTableRead,
    DataSourceCreate,
    DataSourceRead,
    EngineConnectionCreate,
    EngineConnectionRead,
    EngineRef,
    ScopeCreate,
    ScopeRead,
    SourceBindingConfig,
    SourceBindingCreate,
    SourceBindingRead,
    SourceBindingUpdate,
    SpaceCreate,
    SpaceRead,
    SyncRunRead,
)
from intelegent_data_catalog.services.bindings import BindingService
from intelegent_data_catalog.services.data_sources import DataSourceService
from intelegent_data_catalog.services.engines import EngineService
from intelegent_data_catalog.services.scopes import ScopeService
from intelegent_data_catalog.services.spaces import SpaceService
from intelegent_data_catalog.services.tables import TableService
from intelegent_data_catalog.sources import CatalogSource
from intelegent_data_catalog.sources.sync import MetadataSyncService

_SEED: str = "polaris-sales"
_TRINO_CATALOG: str = "polaris"
_SCHEMA: str = "sales"
_SPACE_NAME: str = "Sales"
_SPACE_SLUG: str = "sales"
_SCOPE_NAME: str = "sales"


class SeedLakeRegistrar:
    """Create the sales space and copy Trino's view of the seed tables into it."""

    def __init__(
        self,
        database: CatalogDatabase,
        settings: AppSettings,
        source: CatalogSource,
        logger: logging.Logger,
    ) -> None:
        self._database: CatalogDatabase = database
        self._settings: AppSettings = settings
        self._source: CatalogSource = source
        self._logger: logging.Logger = logger

    def publish(self) -> bool:
        """Register the seed tables. False means Trino did not list them yet."""
        session: Session = self._database.session()
        try:
            repo: RegistryRepository = RegistryRepository(session)
            spaces: SpaceService = SpaceService(repo)
            scopes: ScopeService = ScopeService(repo)
            sources: DataSourceService = DataSourceService(repo)
            tables: TableService = TableService(repo)
            engines: EngineService = EngineService(repo)
            bindings: BindingService = BindingService(repo)
            scope_id: UUID
            trino_binding_id: UUID
            spark_binding_id: UUID
            scope_id, trino_binding_id, spark_binding_id = self._ensure_layout(
                spaces,
                scopes,
                sources,
                engines,
                bindings,
            )
            sync: MetadataSyncService = MetadataSyncService(session, self._source)
            result: SyncRunRead = sync.run(trino_binding_id)
            if result.status is SyncRunStatus.FAILED or result.counts.tables_seen == 0:
                self._logger.info(
                    "seed lake sync status=%s tables=%s error=%s",
                    result.status.value,
                    result.counts.tables_seen,
                    result.sanitized_error,
                )
                return False
            self._attach_spark_queries(tables, bindings, scope_id, spark_binding_id)
            self._logger.info("seed lake projected tables=%s", result.counts.tables_seen)
            return True
        finally:
            session.close()

    def _ensure_layout(
        self,
        spaces: SpaceService,
        scopes: ScopeService,
        sources: DataSourceService,
        engines: EngineService,
        bindings: BindingService,
    ) -> tuple[UUID, UUID, UUID]:
        scope_id: UUID = self._ensure_scope(spaces, scopes)
        source: DataSourceRead = self._ensure_source(sources)
        trino: EngineConnectionRead = self._ensure_engine(
            engines,
            name="Trino",
            engine_type=EngineType.TRINO,
            endpoint=f"http://{self._settings.trino.host}:{self._settings.trino.port}",
        )
        spark: EngineConnectionRead = self._ensure_engine(
            engines,
            name="Spark SQL",
            engine_type=EngineType.SPARK_SQL,
            endpoint=self._settings.query.spark_url,
        )
        trino_binding: SourceBindingRead = self._ensure_binding(
            bindings,
            data_source_id=source.id,
            engine_connection_id=trino.id,
            binding_key=_TRINO_CATALOG,
            scope_id=scope_id,
            schema_allowlist=[_SCHEMA],
        )
        spark_binding: SourceBindingRead = self._ensure_binding(
            bindings,
            data_source_id=source.id,
            engine_connection_id=spark.id,
            binding_key=self._settings.polaris.catalog,
            scope_id=scope_id,
            schema_allowlist=[],
        )
        return scope_id, trino_binding.id, spark_binding.id

    def _ensure_scope(self, spaces: SpaceService, scopes: ScopeService) -> UUID:
        space: SpaceRead | None = _find_space(spaces, _SPACE_SLUG)
        if space is None:
            space = spaces.create_space(
                SpaceCreate(
                    name=_SPACE_NAME,
                    slug=_SPACE_SLUG,
                    description="Seeded Iceberg sales tables",
                )
            )
        scope: ScopeRead | None = None
        candidate: ScopeRead
        for candidate in scopes.list_scopes(space.id):
            if candidate.slug == _SCHEMA:
                scope = candidate
                break
        if scope is None:
            scope = scopes.create_scope(
                space.id,
                ScopeCreate(
                    name=_SCOPE_NAME,
                    slug=_SCHEMA,
                    description="Polaris namespace sales",
                ),
            )
        return scope.id

    def _ensure_source(self, sources: DataSourceService) -> DataSourceRead:
        current: DataSourceRead | None = _find_marked(sources.list_data_sources())
        if current is not None:
            return current
        return sources.create_data_source(
            DataSourceCreate(
                name="Polaris",
                type=DataSourceType.ICEBERG_REST,
                configuration={"seed": _SEED},
            )
        )

    def _ensure_engine(
        self,
        engines: EngineService,
        *,
        name: str,
        engine_type: EngineType,
        endpoint: str,
    ) -> EngineConnectionRead:
        engine: EngineConnectionRead
        for engine in engines.list_engine_connections():
            if engine.engine_type is engine_type and _is_marked(engine.configuration):
                return engine
        return engines.create_engine_connection(
            EngineConnectionCreate(
                name=name,
                engine_type=engine_type,
                endpoint=endpoint,
                configuration={"seed": _SEED},
            )
        )

    def _ensure_binding(
        self,
        bindings: BindingService,
        *,
        data_source_id: UUID,
        engine_connection_id: UUID,
        binding_key: str,
        scope_id: UUID,
        schema_allowlist: list[str],
    ) -> SourceBindingRead:
        current: SourceBindingRead | None = None
        binding: SourceBindingRead
        for binding in bindings.list_source_bindings(data_source_id, engine_connection_id):
            if binding.binding_key == binding_key:
                current = binding
                break
        if current is None:
            return bindings.create_source_binding(
                SourceBindingCreate(
                    data_source_id=data_source_id,
                    engine_connection_id=engine_connection_id,
                    binding_key=binding_key,
                    configuration=SourceBindingConfig(schema_allowlist=schema_allowlist),
                    default_scope_id=scope_id,
                )
            )
        allowlist_matches: bool = list(current.configuration.schema_allowlist) == schema_allowlist
        if current.default_scope_id == scope_id and allowlist_matches:
            return current
        return bindings.update_source_binding(
            current.id,
            SourceBindingUpdate(
                default_scope_id=scope_id,
                configuration=SourceBindingConfig(schema_allowlist=schema_allowlist),
            ),
        )

    def _attach_spark_queries(
        self,
        tables: TableService,
        bindings: BindingService,
        scope_id: UUID,
        spark_binding_id: UUID,
    ) -> None:
        spark_catalog: str = self._settings.polaris.catalog
        table: CatalogTableRead
        for table in tables.list_tables(scope_id):
            if table.physical_namespace != _SCHEMA:
                continue
            stored: list[AssetBindingRead] = bindings.list_asset_bindings(table.id)
            selected: bool = any(
                item.role is BindingRole.QUERY and item.is_selected for item in stored
            )
            if selected:
                continue
            bindings.create_asset_binding(
                table.id,
                AssetBindingCreate(
                    source_engine_binding_id=spark_binding_id,
                    engine_ref=EngineRef(
                        catalog=spark_catalog,
                        namespace=table.physical_namespace,
                        table=table.physical_table,
                    ),
                    engine_ref_key=(
                        f"{spark_catalog}.{table.physical_namespace}.{table.physical_table}"
                    ),
                    role=BindingRole.QUERY,
                    state=BindingState.VALID,
                    is_selected=True,
                ),
            )


def project_seed_lake(
    database: CatalogDatabase,
    settings: AppSettings,
    source: CatalogSource,
    logger: logging.Logger,
    *,
    attempts: int = 8,
    pause_seconds: float = 5.0,
    sleeper: Callable[[float], None] | None = None,
) -> bool:
    """Retry projection until the seed tables are visible or the attempts run out."""
    if not settings.trino.enabled:
        return False
    wait: Callable[[float], None] = sleeper or _sleep
    registrar: SeedLakeRegistrar = SeedLakeRegistrar(database, settings, source, logger)
    attempt: int
    for attempt in range(1, attempts + 1):
        try:
            ready: bool = registrar.publish()
        except Exception:
            logger.exception("seed lake projection failed attempt=%s", attempt)
            ready = False
        if ready:
            return True
        if attempt < attempts:
            wait(pause_seconds)
    logger.warning("seed lake projection stopped; Spaces will not list the sales tables")
    return False


def _find_space(spaces: SpaceService, slug: str) -> SpaceRead | None:
    space: SpaceRead
    for space in spaces.list_spaces():
        if space.slug == slug:
            return space
    return None


def _find_marked(sources: list[DataSourceRead]) -> DataSourceRead | None:
    source: DataSourceRead
    for source in sources:
        if _is_marked(source.configuration):
            return source
    return None


def _is_marked(configuration: dict[str, JsonValue]) -> bool:
    return configuration.get("seed") == _SEED


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
