"""Catalog table and column writes and reads."""

from datetime import datetime
from uuid import UUID, uuid4

from intelegent_data_catalog.assets.sync import AssetSync
from intelegent_data_catalog.exceptions import ConflictError, ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import TableStatus
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    ColumnRow,
    DataSourceRow,
    ScopeRow,
)
from intelegent_data_catalog.registry.identifiers import source_key_from_parts
from intelegent_data_catalog.registry.reads import column_read, dump_model, table_read
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    CatalogTableCreate,
    CatalogTableRead,
    CatalogTableUpdate,
    ColumnCreate,
    ColumnObservation,
    ColumnRead,
    ColumnUpdate,
    SourceSnapshot,
)
from intelegent_data_catalog.services.support import (
    apply_user_context,
    require_row,
    required,
)


class TableService:
    """Table and column persistence against the shared catalog session."""

    def __init__(self, repo: RegistryRepository) -> None:
        self._repo: RegistryRepository = repo

    def create_table(self, scope_id: UUID, body: CatalogTableCreate) -> CatalogTableRead:
        """Create a catalog table. Source key case is preserved."""
        self._scope(scope_id)
        self._data_source(body.data_source_id)
        source_key: str = body.source_key or source_key_from_parts(
            body.physical_catalog,
            body.physical_namespace,
            body.physical_table,
        )
        self._ensure_source_key(body.data_source_id, source_key, None)
        if body.source_native_id is not None:
            self._ensure_native_id(body.data_source_id, body.source_native_id, None)
        now: datetime = utc_now()
        row: CatalogTableRow = CatalogTableRow(
            id=uuid4(),
            scope_id=scope_id,
            data_source_id=body.data_source_id,
            name=body.name,
            display_name=body.display_name,
            description=body.description,
            table_type=body.table_type.value,
            status=body.status.value,
            physical_catalog=body.physical_catalog,
            physical_namespace=body.physical_namespace,
            physical_table=body.physical_table,
            source_ref=dump_model(body.source_ref),
            source_key=source_key,
            source_native_id=body.source_native_id,
            table_format=body.table_format,
            source_description=body.source_description,
            row_count=body.row_count,
            created_at=now,
            updated_at=now,
            last_seen_at=None,
            last_synced_at=None,
        )
        stored: CatalogTableRow = self._repo.add_table(row)
        self._repo.commit()
        return table_read(stored)

    def list_tables(self, scope_id: UUID) -> list[CatalogTableRead]:
        """List tables in a scope."""
        self._scope(scope_id)
        return [table_read(row) for row in self._repo.list_tables(scope_id)]

    def get_table(self, table_id: UUID) -> CatalogTableRead:
        """Return one catalog table."""
        return table_read(self._table(table_id))

    def find_table_by_source_key(
        self,
        data_source_id: UUID,
        source_key: str,
    ) -> CatalogTableRead | None:
        """Return the table for a source key, or none when it is not registered."""
        row: CatalogTableRow | None = self._repo.find_table_by_source_key(
            data_source_id,
            source_key,
        )
        if row is None:
            return None
        return table_read(row)

    def update_table(self, table_id: UUID, patch: CatalogTableUpdate) -> CatalogTableRead:
        """Patch user fields and the manual scope assignment."""
        row: CatalogTableRow = self._table(table_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = required(patch.name, "name")
        if "display_name" in changes:
            row.display_name = patch.display_name
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            apply_user_context(row, patch.context)
        if "status" in changes:
            row.status = required(patch.status, "status").value
        if "table_format" in changes:
            row.table_format = patch.table_format
        if "scope_id" in changes:
            scope_id: UUID = required(patch.scope_id, "scope_id")
            self._scope(scope_id)
            row.scope_id = scope_id
        row.updated_at = utc_now()
        self._repo.commit()
        return table_read(row)

    def delete_table(self, table_id: UUID) -> None:
        """Delete a table that is not part of a relationship."""
        row: CatalogTableRow = self._table(table_id)
        if self._repo.count_relationships_for_table(table_id) > 0:
            raise ConflictError("table is used by a relationship")
        self._repo.delete_asset_bindings_for_table(table_id)
        self._repo.delete_columns_for_table(table_id)
        sync: AssetSync | None = AssetSync.for_row(row)
        if sync is not None:
            sync.delete_table(row)
        self._repo.delete(row)
        self._repo.commit()

    def apply_source_snapshot(self, table_id: UUID, snapshot: SourceSnapshot) -> CatalogTableRead:
        """Apply imported fields. User description, scope, and DEPRECATED stay in place."""
        row: CatalogTableRow = self._table(table_id)
        changes: dict[str, object] = snapshot.model_dump(exclude_unset=True)
        if "source_description" in changes:
            row.source_description = snapshot.source_description
        if "row_count" in changes:
            row.row_count = snapshot.row_count
        if "status" in changes and snapshot.status is not None:
            self._apply_snapshot_status(row, snapshot.status)
        observed_at: datetime = utc_now()
        row.last_seen_at = _observed_time(
            changes,
            "last_seen_at",
            snapshot.last_seen_at,
            observed_at,
        )
        row.last_synced_at = _observed_time(
            changes,
            "last_synced_at",
            snapshot.last_synced_at,
            observed_at,
        )
        if "columns" in changes:
            observation: ColumnObservation
            for observation in snapshot.columns:
                self._apply_column_observation(row.id, observation)
        row.updated_at = observed_at
        self._repo.commit()
        return table_read(row)

    def create_column(self, table_id: UUID, body: ColumnCreate) -> ColumnRead:
        """Add a column to a table."""
        self._table(table_id)
        self._ensure_column_name(table_id, body.name, None)
        now: datetime = utc_now()
        row: ColumnRow = ColumnRow(
            id=uuid4(),
            table_id=table_id,
            name=body.name,
            description=body.description,
            data_type=body.data_type,
            native_data_type=body.native_data_type,
            discovered_data_type=body.discovered_data_type,
            metadata_origin=body.metadata_origin.value,
            source_description=body.source_description,
            nullable=body.nullable,
            ordinal_position=body.ordinal_position,
            is_primary_key=body.is_primary_key,
            is_unique=body.is_unique,
            created_at=now,
            updated_at=now,
            last_seen_at=None,
        )
        stored: ColumnRow = self._repo.add_column(row)
        self._repo.commit()
        return column_read(stored)

    def get_column(self, column_id: UUID) -> ColumnRead:
        """Return one column."""
        return column_read(self._column(column_id))

    def list_columns(self, table_id: UUID) -> list[ColumnRead]:
        """List columns on a table."""
        self._table(table_id)
        return [column_read(row) for row in self._repo.list_columns(table_id)]

    def update_column(self, column_id: UUID, patch: ColumnUpdate) -> ColumnRead:
        """Patch user column fields. Null constraints mean unknown."""
        row: ColumnRow = self._column(column_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            apply_user_context(row, patch.context)
        if "data_type" in changes:
            row.data_type = patch.data_type
        if "nullable" in changes:
            row.nullable = patch.nullable
        if "is_primary_key" in changes:
            row.is_primary_key = patch.is_primary_key
        if "is_unique" in changes:
            row.is_unique = patch.is_unique
        row.updated_at = utc_now()
        self._repo.commit()
        return column_read(row)

    def delete_column(self, column_id: UUID) -> None:
        """Delete a column that is not used by a relationship mapping."""
        row: ColumnRow = self._column(column_id)
        if self._repo.count_mappings_for_column(column_id) > 0:
            raise ConflictError("column is used by a relationship")
        self._repo.delete(row)
        self._repo.commit()

    def _apply_snapshot_status(self, row: CatalogTableRow, status: TableStatus) -> None:
        if row.status == TableStatus.DEPRECATED.value:
            return
        if status is TableStatus.DEPRECATED:
            raise ValidationAppError(
                "imported snapshots cannot mark a table DEPRECATED",
                field="status",
            )
        row.status = status.value

    def _apply_column_observation(self, table_id: UUID, observation: ColumnObservation) -> None:
        column: ColumnRow | None = self._repo.find_column_by_name(table_id, observation.name)
        if column is None:
            raise ValidationAppError(
                f"column is not on the table: {observation.name}",
                field="columns",
            )
        changes: dict[str, object] = observation.model_dump(exclude_unset=True)
        if "discovered_data_type" in changes:
            column.discovered_data_type = observation.discovered_data_type
        if "native_data_type" in changes:
            column.native_data_type = observation.native_data_type
        if "source_description" in changes:
            column.source_description = observation.source_description
        if "nullable" in changes:
            column.nullable = observation.nullable
        if "ordinal_position" in changes and observation.ordinal_position is not None:
            column.ordinal_position = observation.ordinal_position
        if "is_primary_key" in changes:
            column.is_primary_key = observation.is_primary_key
        if "is_unique" in changes:
            column.is_unique = observation.is_unique
        if "metadata_origin" in changes and observation.metadata_origin is not None:
            column.metadata_origin = observation.metadata_origin.value
        column.updated_at = utc_now()

    def _ensure_source_key(
        self,
        data_source_id: UUID,
        source_key: str,
        current_id: UUID | None,
    ) -> None:
        existing: CatalogTableRow | None = self._repo.find_table_by_source_key(
            data_source_id,
            source_key,
        )
        if existing is not None and existing.id != current_id:
            raise ConflictError("source_key already exists for this data source")

    def _ensure_native_id(
        self,
        data_source_id: UUID,
        source_native_id: str,
        current_id: UUID | None,
    ) -> None:
        existing: CatalogTableRow | None = self._repo.find_table_by_native_id(
            data_source_id,
            source_native_id,
        )
        if existing is not None and existing.id != current_id:
            raise ConflictError("source_native_id already exists for this data source")

    def _ensure_column_name(self, table_id: UUID, name: str, current_id: UUID | None) -> None:
        existing: ColumnRow | None = self._repo.find_column_by_name(table_id, name)
        if existing is not None and existing.id != current_id:
            raise ConflictError(f"column name already exists on this table: {name}")

    def _scope(self, scope_id: UUID) -> ScopeRow:
        row: ScopeRow | None = self._repo.get_scope(scope_id)
        return require_row(row, "scope", scope_id)

    def _data_source(self, data_source_id: UUID) -> DataSourceRow:
        row: DataSourceRow | None = self._repo.get_data_source(data_source_id)
        return require_row(row, "data source", data_source_id)

    def _table(self, table_id: UUID) -> CatalogTableRow:
        row: CatalogTableRow | None = self._repo.get_table(table_id)
        return require_row(row, "table", table_id)

    def _column(self, column_id: UUID) -> ColumnRow:
        row: ColumnRow | None = self._repo.get_column(column_id)
        return require_row(row, "column", column_id)


def _observed_time(
    changes: dict[str, object],
    field: str,
    explicit: datetime | None,
    fallback: datetime,
) -> datetime:
    if field in changes and explicit is not None:
        return explicit
    return fallback
