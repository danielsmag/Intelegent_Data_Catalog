"""Primitive columns taken from an Iceberg load-table document."""

from pydantic import JsonValue, ValidationError

from intelegent_data_catalog.assets.profiling.sql import PRIMITIVE_TYPES, is_identifier
from intelegent_data_catalog.catalog.repositories.polaris import (
    IcebergField,
    IcebergSchema,
)
from intelegent_data_catalog.catalog.repositories.polaris import (
    IcebergTableMetadata as IcebergMetadata,
)
from intelegent_data_catalog.exceptions import CatalogError


def primitive_columns(raw: dict[str, JsonValue]) -> list[tuple[str, str]]:
    """Return ``(name, type)`` for primitive fields with simple names."""
    schema: IcebergSchema | None = _current_schema(_metadata(raw))
    if schema is None:
        return []
    found: list[tuple[str, str]] = []
    field: IcebergField
    for field in schema.fields:
        if not isinstance(field.field_type, str) or field.field_type not in PRIMITIVE_TYPES:
            continue
        if not is_identifier(field.name):
            continue
        found.append((field.name, field.field_type))
    return found


def _metadata(raw: dict[str, JsonValue]) -> IcebergMetadata:
    payload: JsonValue | None = raw.get("metadata")
    try:
        return IcebergMetadata.model_validate(payload)
    except ValidationError as err:
        raise CatalogError("Iceberg metadata did not include a schema") from err


def _current_schema(metadata: IcebergMetadata) -> IcebergSchema | None:
    if len(metadata.schemas) == 0:
        return None
    if metadata.current_schema_id is None:
        return metadata.schemas[-1]
    schema: IcebergSchema
    for schema in metadata.schemas:
        if schema.schema_id == metadata.current_schema_id:
            return schema
    return metadata.schemas[-1]
