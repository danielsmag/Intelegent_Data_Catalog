"""Trino discovery sync into the catalog. Postgres remains the authority."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import TrinoUnavailableError, ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    AssetStatus,
    BindingRole,
    BindingState,
    CapabilitySupport,
    EngineType,
    MetadataOrigin,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    SyncRunStatus,
    TableStatus,
)
from intelegent_data_catalog.persistence.models import (
    AssetEngineBindingRow,
    AssetRow,
    CatalogTableRow,
    ColumnRow,
    DataSourceRow,
    MetadataSyncRunRow,
    RelationshipColumnMappingRow,
    RelationshipRow,
    SourceEngineBindingRow,
)
from intelegent_data_catalog.registry.reads import dump_model, sync_run_read
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    EngineRef,
    ScanScope,
    SyncCounts,
    SyncRunRead,
)
from intelegent_data_catalog.sources import CatalogSource, RelationshipEnricher
from intelegent_data_catalog.sources.discovery import (
    DiscoveryBatch,
    DiscoveryRequest,
    EnrichmentBatch,
    ImportedForeignKey,
    SourceColumnRecord,
    SourceTableRecord,
)
from intelegent_data_catalog.sources.trino.client import sanitize_engine_message


class MetadataSyncService:
    """Scan one Trino binding and upsert catalog rows without clobbering user edits."""

    def __init__(
        self,
        session: Session,
        source: CatalogSource,
        enricher: RelationshipEnricher | None = None,
        after_success: Callable[[], None] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._session: Session = session
        self._repo: RegistryRepository = RegistryRepository(session)
        self._source: CatalogSource = source
        self._enricher: RelationshipEnricher | None = enricher
        self._after_success: Callable[[], None] | None = after_success
        self._sleeper: Callable[[float], None] = sleeper or _noop
        self._native_types: dict[str, str] = {}
        self._seen_columns: dict[str, set[str]] = {}

    def run(self, source_binding_id: UUID) -> SyncRunRead:
        """Run one discovery scan. Partial and failed scans do not mark tables missing."""
        binding: SourceEngineBindingRow = self._require_binding(source_binding_id)
        if binding.engine_type != EngineType.TRINO.value:
            raise ValidationAppError(
                "metadata sync runs attach to a TRINO source binding",
                field="source_engine_binding_id",
            )
        data_source: DataSourceRow = self._require_source(binding.data_source_id)
        started: datetime = utc_now()
        run: MetadataSyncRunRow = self._repo.add_sync_run(
            MetadataSyncRunRow(
                id=uuid4(),
                source_engine_binding_id=binding.id,
                started_at=started,
                finished_at=None,
                status=SyncRunStatus.RUNNING.value,
                scan_scope=dump_model(
                    ScanScope(
                        catalogs=[binding.binding_key],
                        schemas=_string_list(binding.configuration.get("schema_allowlist")),
                    )
                ),
                counts=dump_model(SyncCounts()),
                sanitized_error=None,
                checkpoint=None,
            )
        )
        self._repo.commit()
        request: DiscoveryRequest = DiscoveryRequest(
            catalog=binding.binding_key,
            schema_allowlist=_string_list(binding.configuration.get("schema_allowlist")),
            max_tables=500,
        )
        try:
            batch: DiscoveryBatch = self._discover(request)
        except TrinoUnavailableError as err:
            return self._finish(
                run,
                SyncRunStatus.FAILED,
                tables_seen=0,
                columns_seen=0,
                errors=1,
                message=sanitize_engine_message(str(err)),
                reconcile=False,
                binding=binding,
                seen=set(),
            )
        self._seen_columns = {}
        self._native_types = {}
        batch = self._maybe_enrich(batch, data_source, request)
        seen: set[str] = set()
        errors: int = 0
        columns_seen: int = 0
        message: str | None = None
        table: SourceTableRecord
        for table in batch.tables:
            try:
                columns_seen += self._upsert_table(binding, data_source, table)
                seen.add(_source_key(table.catalog, table.namespace, table.name))
                self._repo.commit()
            except Exception as err:
                self._session.rollback()
                errors += 1
                message = sanitize_engine_message(str(err))
        if batch.relationship_capability is CapabilitySupport.SUPPORTED and errors == 0:
            try:
                self._import_relationships(data_source.id, binding.binding_key, batch.relationships)
                self._repo.commit()
            except Exception as err:
                self._session.rollback()
                errors += 1
                message = sanitize_engine_message(str(err))
        self._store_capability(binding, batch.relationship_capability)
        reconcile: bool = errors == 0 and not batch.truncated
        status: SyncRunStatus = SyncRunStatus.SUCCEEDED
        if errors > 0 and seen:
            status = SyncRunStatus.PARTIAL
        elif errors > 0:
            status = SyncRunStatus.FAILED
        elif batch.truncated:
            status = SyncRunStatus.PARTIAL
            message = "scan stopped at the table limit; missing assets were left unchanged"
        finished: SyncRunRead = self._finish(
            run,
            status,
            tables_seen=len(seen),
            columns_seen=columns_seen,
            errors=errors,
            message=message,
            reconcile=reconcile,
            binding=binding,
            seen=seen,
        )
        if status is SyncRunStatus.SUCCEEDED and self._after_success is not None:
            try:
                self._after_success()
            except Exception:
                return finished
        return finished

    def _discover(self, request: DiscoveryRequest) -> DiscoveryBatch:
        try:
            return self._source.discover(request)
        except TrinoUnavailableError:
            self._sleeper(0.2)
            return self._source.discover(request)

    def _maybe_enrich(
        self,
        batch: DiscoveryBatch,
        data_source: DataSourceRow,
        request: DiscoveryRequest,
    ) -> DiscoveryBatch:
        if batch.relationship_capability is CapabilitySupport.SUPPORTED:
            return batch
        if self._enricher is None:
            return batch
        enriched: EnrichmentBatch | None = self._enricher.enrich(data_source, request)
        if enriched is None:
            return batch
        if enriched.relationship_capability is not CapabilitySupport.SUPPORTED:
            return batch
        native: dict[str, str] = enriched.native_types
        self._native_types = native
        return batch.model_copy(
            update={
                "relationship_capability": CapabilitySupport.SUPPORTED,
                "relationships": enriched.relationships,
            }
        )

    def _upsert_table(
        self,
        binding: SourceEngineBindingRow,
        data_source: DataSourceRow,
        observed: SourceTableRecord,
    ) -> int:
        source_key: str = _source_key(observed.catalog, observed.namespace, observed.name)
        now: datetime = utc_now()
        existing: CatalogTableRow | None = self._repo.find_table_by_source_key(
            data_source.id,
            source_key,
        )
        table_format: str | None = "ICEBERG" if data_source.type == "ICEBERG_REST" else None
        if existing is None:
            existing = self._repo.add_table(
                CatalogTableRow(
                    id=uuid4(),
                    data_source_id=data_source.id,
                    name=observed.name,
                    display_name=None,
                    description=None,
                    table_type=observed.table_type.value,
                    status=TableStatus.ACTIVE.value,
                    physical_catalog=observed.catalog,
                    physical_namespace=observed.namespace,
                    physical_table=observed.name,
                    source_ref={
                        "catalog": observed.catalog,
                        "namespace": observed.namespace,
                        "table": observed.name,
                    },
                    source_key=source_key,
                    source_native_id=None,
                    table_format=table_format if observed.table_type.value == "TABLE" else None,
                    source_description=observed.source_description,
                    row_count=None,
                    created_at=now,
                    updated_at=now,
                    last_seen_at=now,
                    last_synced_at=now,
                )
            )
        else:
            if existing.status == TableStatus.MISSING.value:
                existing.status = TableStatus.ACTIVE.value
                self._align_asset(existing, AssetStatus.ACTIVE.value)
            existing.table_type = observed.table_type.value
            existing.physical_catalog = observed.catalog
            existing.physical_namespace = observed.namespace
            existing.physical_table = observed.name
            existing.source_ref = {
                "catalog": observed.catalog,
                "namespace": observed.namespace,
                "table": observed.name,
            }
            existing.source_description = observed.source_description
            existing.last_seen_at = now
            existing.last_synced_at = now
            existing.updated_at = now
        self._upsert_columns(existing, observed.columns, now)
        self._upsert_discovery_binding(binding, existing, observed, now)
        self._seen_columns[source_key] = {column.name for column in observed.columns}
        return len(observed.columns)

    def _upsert_columns(
        self,
        table: CatalogTableRow,
        columns: list[SourceColumnRecord],
        now: datetime,
    ) -> None:
        column: SourceColumnRecord
        for column in columns:
            current: ColumnRow | None = self._repo.find_column_by_name(table.id, column.name)
            native: str | None = self._native_types.get(
                f"{table.physical_namespace}.{table.physical_table}.{column.name}"
            )
            if current is None:
                self._repo.add_column(
                    ColumnRow(
                        id=uuid4(),
                        table_id=table.id,
                        name=column.name,
                        description=None,
                        data_type=None,
                        native_data_type=native,
                        discovered_data_type=column.discovered_data_type,
                        metadata_origin=MetadataOrigin.TRINO.value,
                        source_description=column.source_description,
                        nullable=column.nullable,
                        ordinal_position=column.ordinal_position,
                        is_primary_key=None,
                        is_unique=None,
                        created_at=now,
                        updated_at=now,
                        last_seen_at=now,
                    )
                )
                continue
            current.discovered_data_type = column.discovered_data_type
            current.source_description = column.source_description
            current.nullable = column.nullable
            current.ordinal_position = column.ordinal_position
            if native is not None:
                current.native_data_type = native
            if current.metadata_origin == MetadataOrigin.UNKNOWN.value:
                current.metadata_origin = MetadataOrigin.TRINO.value
            current.last_seen_at = now
            current.updated_at = now

    def _upsert_discovery_binding(
        self,
        binding: SourceEngineBindingRow,
        table: CatalogTableRow,
        observed: SourceTableRecord,
        now: datetime,
    ) -> None:
        engine_ref_key: str = _source_key(observed.catalog, observed.namespace, observed.name)
        current: AssetEngineBindingRow | None = self._repo.find_asset_binding_by_key(
            binding.id,
            engine_ref_key,
        )
        engine_ref: dict[str, object] = dump_model(
            EngineRef(catalog=observed.catalog, namespace=observed.namespace, table=observed.name)
        )
        if current is None:
            self._repo.add_asset_binding(
                AssetEngineBindingRow(
                    id=uuid4(),
                    catalog_table_id=table.id,
                    data_source_id=table.data_source_id,
                    source_engine_binding_id=binding.id,
                    engine_type=EngineType.TRINO.value,
                    engine_ref=engine_ref,
                    engine_ref_key=engine_ref_key,
                    role=BindingRole.DISCOVERY.value,
                    state=BindingState.VALID.value,
                    is_selected=False,
                    last_validated_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        current.engine_ref = engine_ref
        current.state = BindingState.VALID.value
        current.last_validated_at = now
        current.updated_at = now

    def _disable_removed_columns(self, table_id: UUID, seen_names: set[str], now: datetime) -> None:
        column: ColumnRow
        for column in self._repo.list_columns(table_id):
            if column.name in seen_names:
                continue
            relationship: RelationshipRow
            for relationship in self._repo.list_relationships_for_column(column.id):
                if relationship.status == RelationshipStatus.DISABLED.value:
                    continue
                relationship.status = RelationshipStatus.DISABLED.value
                relationship.updated_at = now

    def _import_relationships(
        self,
        data_source_id: UUID,
        catalog: str,
        keys: list[ImportedForeignKey],
    ) -> None:
        grouped: dict[str, list[ImportedForeignKey]] = {}
        key: ImportedForeignKey
        for key in keys:
            grouped.setdefault(key.constraint_name, []).append(key)
        imported_pairs: set[tuple[UUID, UUID, tuple[tuple[UUID, UUID], ...]]] = set()
        constraint_name: str
        parts: list[ImportedForeignKey]
        for constraint_name, parts in grouped.items():
            ordered: list[ImportedForeignKey] = sorted(parts, key=lambda item: item.position)
            first: ImportedForeignKey = ordered[0]
            source_table: CatalogTableRow | None = self._repo.find_table_by_source_key(
                data_source_id,
                _source_key(catalog, first.source_namespace, first.source_table),
            )
            target_table: CatalogTableRow | None = self._repo.find_table_by_source_key(
                data_source_id,
                _source_key(catalog, first.target_namespace, first.target_table),
            )
            if source_table is None or target_table is None:
                continue
            pairs: list[tuple[UUID, UUID]] = []
            part: ImportedForeignKey
            for part in ordered:
                source_column: ColumnRow | None = self._repo.find_column_by_name(
                    source_table.id,
                    part.source_column,
                )
                target_column: ColumnRow | None = self._repo.find_column_by_name(
                    target_table.id,
                    part.target_column,
                )
                if source_column is None or target_column is None:
                    pairs = []
                    break
                pairs.append((source_column.id, target_column.id))
            if not pairs:
                continue
            pair_key: tuple[tuple[UUID, UUID], ...] = tuple(pairs)
            imported_pairs.add((source_table.id, target_table.id, pair_key))
            if self._relationship_exists(source_table.id, target_table.id, pair_key):
                continue
            now: datetime = utc_now()
            created: RelationshipRow = self._repo.add_relationship(
                RelationshipRow(
                    id=uuid4(),
                    source_table_id=source_table.id,
                    target_table_id=target_table.id,
                    relationship_type=RelationshipType.FOREIGN_KEY.value,
                    origin=RelationshipOrigin.IMPORT.value,
                    status=RelationshipStatus.UNVERIFIED.value,
                    description=constraint_name,
                    created_at=now,
                    updated_at=now,
                )
            )
            index: int
            pair: tuple[UUID, UUID]
            for index, pair in enumerate(pairs):
                self._repo.add_mapping(
                    RelationshipColumnMappingRow(
                        id=uuid4(),
                        relationship_id=created.id,
                        source_column_id=pair[0],
                        target_column_id=pair[1],
                        position=index,
                    )
                )

    def _relationship_exists(
        self,
        source_table_id: UUID,
        target_table_id: UUID,
        pairs: tuple[tuple[UUID, UUID], ...],
    ) -> bool:
        relationship: RelationshipRow
        for relationship in self._repo.list_relationships_for_table(source_table_id):
            if relationship.target_table_id != target_table_id:
                continue
            if relationship.source_table_id != source_table_id:
                continue
            current: list[RelationshipColumnMappingRow] = self._repo.list_mappings(relationship.id)
            current_pairs: tuple[tuple[UUID, UUID], ...] = tuple(
                (item.source_column_id, item.target_column_id) for item in current
            )
            if current_pairs == pairs:
                return True
        return False

    def _tombstone_columns(self, data_source_id: UUID) -> None:
        source_key: str
        names: set[str]
        for source_key, names in self._seen_columns.items():
            table: CatalogTableRow | None = self._repo.find_table_by_source_key(
                data_source_id,
                source_key,
            )
            if table is None:
                continue
            self._disable_removed_columns(table.id, names, utc_now())

    def _store_capability(
        self, binding: SourceEngineBindingRow, capability: CapabilitySupport
    ) -> None:
        current: dict[str, object] = dict(binding.capabilities)
        current["columns"] = CapabilitySupport.SUPPORTED.value
        current["relationships"] = capability.value
        if "statistics" not in current:
            current["statistics"] = CapabilitySupport.UNKNOWN.value
        if "constraints" not in current:
            current["constraints"] = capability.value
        binding.capabilities = current
        binding.updated_at = utc_now()

    def _finish(
        self,
        run: MetadataSyncRunRow,
        status: SyncRunStatus,
        *,
        tables_seen: int,
        columns_seen: int,
        errors: int,
        message: str | None,
        reconcile: bool,
        binding: SourceEngineBindingRow,
        seen: set[str],
    ) -> SyncRunRead:
        if reconcile:
            self._mark_missing(binding, seen)
            self._tombstone_columns(binding.data_source_id)
        now: datetime = utc_now()
        run.status = status.value
        run.finished_at = now
        run.counts = dump_model(
            SyncCounts(tables_seen=tables_seen, columns_seen=columns_seen, errors=errors)
        )
        run.sanitized_error = message
        self._repo.commit()
        return sync_run_read(run)

    def _mark_missing(self, binding: SourceEngineBindingRow, seen: set[str]) -> None:
        asset: AssetEngineBindingRow
        for asset in self._repo.list_asset_bindings_for_source(binding.id):
            if asset.role != BindingRole.DISCOVERY.value:
                continue
            table: CatalogTableRow | None = self._repo.get_table(asset.catalog_table_id)
            if table is None or table.source_key in seen:
                continue
            if table.status == TableStatus.DEPRECATED.value:
                continue
            table.status = TableStatus.MISSING.value
            table.updated_at = utc_now()
            self._align_asset(table, AssetStatus.MISSING.value)

    def _align_asset(self, table: CatalogTableRow, status: str) -> None:
        """Copy discovery status onto the asset. A user off switch stays off."""
        if table.asset_id is None:
            return
        asset: AssetRow | None = self._session.get(AssetRow, table.asset_id)
        if asset is None or asset.status == AssetStatus.DEPRECATED.value:
            return
        asset.status = status
        asset.updated_at = utc_now()

    def _require_binding(self, source_binding_id: UUID) -> SourceEngineBindingRow:
        binding: SourceEngineBindingRow | None = self._repo.get_source_binding(source_binding_id)
        if binding is None:
            from intelegent_data_catalog.exceptions import EntityNotFoundError

            raise EntityNotFoundError("source binding", str(source_binding_id))
        return binding

    def _require_source(self, data_source_id: UUID) -> DataSourceRow:
        source: DataSourceRow | None = self._repo.get_data_source(data_source_id)
        if source is None:
            from intelegent_data_catalog.exceptions import EntityNotFoundError

            raise EntityNotFoundError("data source", str(data_source_id))
        return source


def _source_key(catalog: str, namespace: str, table: str) -> str:
    return f"{catalog}.{namespace}.{table}"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    item: object
    for item in value:
        if isinstance(item, str):
            names.append(item)
    return names


def _noop(_seconds: float) -> None:
    return None
