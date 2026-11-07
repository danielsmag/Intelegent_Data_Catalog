"""Map catalog rows to API models."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError

from intelegent_data_catalog.exceptions import PersistenceUnavailableError
from intelegent_data_catalog.persistence.enums import (
    BindingRole,
    BindingState,
    ContextOrigin,
    DataSourceType,
    EngineConnectionStatus,
    EngineType,
    MetadataOrigin,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    SyncRunStatus,
    TableKind,
    TableStatus,
)
from intelegent_data_catalog.persistence.models import (
    AgentEngineBindingRow,
    CatalogTableRow,
    ColumnRow,
    DataSourceRow,
    EngineConnectionRow,
    MetadataSyncRunRow,
    RelationshipColumnMappingRow,
    RelationshipRow,
    SourceEngineBindingRow,
)
from intelegent_data_catalog.registry.schemas import (
    AgentBindingRead,
    CatalogTableRead,
    ColumnMappingRead,
    ColumnRead,
    DataSourceRead,
    EngineConnectionRead,
    EngineRef,
    RelationshipRead,
    ScanScope,
    SourceBindingConfig,
    SourceBindingRead,
    SourceCapabilities,
    SourceRef,
    SyncCounts,
    SyncRunRead,
)


def dump_model(model: BaseModel) -> dict[str, object]:
    """Serialize a model to a JSON object for a database column."""
    raw: object = model.model_dump(mode="json")
    if not isinstance(raw, dict):
        raise PersistenceUnavailableError("stored catalog JSON is invalid")
    stored: dict[str, object] = {}
    key: object
    value: object
    for key, value in raw.items():
        if not isinstance(key, str):
            raise PersistenceUnavailableError("stored catalog JSON is invalid")
        stored[key] = value
    return stored


def dump_json_dict(value: dict[str, JsonValue]) -> dict[str, object]:
    """Copy a JSON object into the mapping type stored on a row."""
    stored: dict[str, object] = {}
    key: str
    item: JsonValue
    for key, item in value.items():
        stored[key] = item
    return stored


def data_source_read(row: DataSourceRow) -> DataSourceRead:
    """Map a data source row."""
    return DataSourceRead(
        id=row.id,
        name=row.name,
        type=DataSourceType(row.type),
        configuration=_json_dict(row.configuration),
        credential_ref=row.credential_ref,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def table_read(row: CatalogTableRow) -> CatalogTableRead:
    """Map a catalog table row."""
    return CatalogTableRead(
        id=row.id,
        agent_id=row.agent_id,
        data_source_id=row.data_source_id,
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        context=row.context,
        context_origin=_context_origin(row.context_origin),
        table_type=TableKind(row.table_type),
        status=TableStatus(row.status),
        physical_catalog=row.physical_catalog,
        physical_namespace=row.physical_namespace,
        physical_table=row.physical_table,
        source_ref=_load(SourceRef, row.source_ref),
        source_key=row.source_key,
        source_native_id=row.source_native_id,
        table_format=row.table_format,
        source_description=row.source_description,
        row_count=row.row_count,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        last_seen_at=_aware_optional(row.last_seen_at),
        last_synced_at=_aware_optional(row.last_synced_at),
    )


def column_read(row: ColumnRow) -> ColumnRead:
    """Map a column row."""
    return ColumnRead(
        id=row.id,
        table_id=row.table_id,
        name=row.name,
        description=row.description,
        context=row.context,
        context_origin=_context_origin(row.context_origin),
        data_type=row.data_type,
        native_data_type=row.native_data_type,
        discovered_data_type=row.discovered_data_type,
        metadata_origin=MetadataOrigin(row.metadata_origin),
        source_description=row.source_description,
        nullable=row.nullable,
        ordinal_position=row.ordinal_position,
        is_primary_key=row.is_primary_key,
        is_unique=row.is_unique,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        last_seen_at=_aware_optional(row.last_seen_at),
    )


def relationship_read(
    row: RelationshipRow,
    mappings: list[RelationshipColumnMappingRow],
    column_names: dict[UUID, str],
) -> RelationshipRead:
    """Map a relationship and its ordered column pairs."""
    mapped: list[ColumnMappingRead] = []
    mapping: RelationshipColumnMappingRow
    for mapping in mappings:
        mapped.append(
            ColumnMappingRead(
                id=mapping.id,
                source_column_id=mapping.source_column_id,
                target_column_id=mapping.target_column_id,
                source_column_name=column_names[mapping.source_column_id],
                target_column_name=column_names[mapping.target_column_id],
                position=mapping.position,
            )
        )
    return RelationshipRead(
        id=row.id,
        source_table_id=row.source_table_id,
        target_table_id=row.target_table_id,
        relationship_type=RelationshipType(row.relationship_type),
        origin=RelationshipOrigin(row.origin),
        status=RelationshipStatus(row.status),
        description=row.description,
        column_mappings=mapped,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def engine_connection_read(row: EngineConnectionRow) -> EngineConnectionRead:
    """Map an engine connection row."""
    return EngineConnectionRead(
        id=row.id,
        name=row.name,
        engine_type=EngineType(row.engine_type),
        endpoint=row.endpoint,
        credential_ref=row.credential_ref,
        configuration=_json_dict(row.configuration),
        status=EngineConnectionStatus(row.status),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def source_binding_read(row: SourceEngineBindingRow) -> SourceBindingRead:
    """Map a source engine binding row."""
    return SourceBindingRead(
        id=row.id,
        data_source_id=row.data_source_id,
        engine_connection_id=row.engine_connection_id,
        binding_key=row.binding_key,
        engine_type=EngineType(row.engine_type),
        configuration=_load(SourceBindingConfig, row.configuration),
        capabilities=_load(SourceCapabilities, row.capabilities),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def agent_binding_read(row: AgentEngineBindingRow) -> AgentBindingRead:
    """Map an agent engine binding row."""
    return AgentBindingRead(
        id=row.id,
        catalog_table_id=row.catalog_table_id,
        data_source_id=row.data_source_id,
        source_engine_binding_id=row.source_engine_binding_id,
        engine_type=EngineType(row.engine_type),
        engine_ref=_load(EngineRef, row.engine_ref),
        engine_ref_key=row.engine_ref_key,
        role=BindingRole(row.role),
        state=BindingState(row.state),
        is_selected=row.is_selected,
        last_validated_at=_aware_optional(row.last_validated_at),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def sync_run_read(row: MetadataSyncRunRow) -> SyncRunRead:
    """Map a metadata sync run row."""
    return SyncRunRead(
        id=row.id,
        source_engine_binding_id=row.source_engine_binding_id,
        started_at=_aware(row.started_at),
        finished_at=_aware_optional(row.finished_at),
        status=SyncRunStatus(row.status),
        scan_scope=_load(ScanScope, row.scan_scope),
        counts=_load(SyncCounts, row.counts),
        sanitized_error=row.sanitized_error,
        checkpoint=_checkpoint(row.checkpoint),
    )


def _context_origin(value: str | None) -> ContextOrigin | None:
    if value is None:
        return None
    return ContextOrigin(value)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _aware_optional(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _aware(value)


def _json_dict(value: dict[str, object]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], value)


def _checkpoint(value: dict[str, object] | None) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    return _json_dict(value)


def _load[ModelT: BaseModel](model_type: type[ModelT], payload: dict[str, object]) -> ModelT:
    try:
        return model_type.model_validate(payload)
    except ValidationError as err:
        raise PersistenceUnavailableError("stored catalog JSON is invalid") from err
