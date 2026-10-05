"""Copy tables and schemas from a catalog source into a space scope."""

from uuid import UUID

from pydantic import JsonValue

from intelegent_data_catalog.catalog.models import (
    POLARIS_SOURCE_ID,
    TRINO_SOURCE_ID,
    ObservedColumn,
    ObservedTable,
)
from intelegent_data_catalog.catalog.services.explorer_service import ExplorerService
from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.persistence.enums import DataSourceType, MetadataOrigin, TableKind
from intelegent_data_catalog.registry.identifiers import source_key_from_parts
from intelegent_data_catalog.registry.schemas import (
    AttachmentItem,
    AttachmentOutcome,
    CatalogTableCreate,
    CatalogTableRead,
    ColumnCreate,
    DataSourceCreate,
    DataSourceRead,
    ScopeAttachmentCreate,
    ScopeAttachmentResult,
    SourceRef,
)
from intelegent_data_catalog.registry.service import RegistryService

_MAX_TABLES: int = 500
_TYPE_LIMIT: int = 512
_CONNECTOR_TYPES: dict[str, DataSourceType] = {
    "iceberg": DataSourceType.ICEBERG_REST,
    "postgresql": DataSourceType.POSTGRES,
    "postgres": DataSourceType.POSTGRES,
    "mysql": DataSourceType.MYSQL,
    "snowflake": DataSourceType.SNOWFLAKE,
    "bigquery": DataSourceType.BIGQUERY,
}


class ScopeAttachmentService:
    """Registers source tables under a scope without moving tables that already exist."""

    def __init__(self, registry: RegistryService, explorer: ExplorerService) -> None:
        self._registry: RegistryService = registry
        self._explorer: ExplorerService = explorer

    def attach(self, scope_id: UUID, body: ScopeAttachmentCreate) -> ScopeAttachmentResult:
        """Attach selected tables, or every table in the schema when tables is omitted."""
        self._registry.get_scope(scope_id)
        observed: list[ObservedTable] = self._explorer.observed(
            body.source_id,
            body.catalog,
            body.namespace,
            body.tables,
        )
        if len(observed) > _MAX_TABLES:
            raise ValidationAppError(
                f"schema has more than {_MAX_TABLES} tables; attach a smaller selection",
                field="tables",
            )
        connector: str = self._explorer.connector_name(body.source_id, body.catalog)
        source: DataSourceRead = self._data_source(body.source_id, body.catalog, connector)
        source_type: DataSourceType = source.type
        table_format: str | None = (
            "ICEBERG" if source_type == DataSourceType.ICEBERG_REST else None
        )
        origin: MetadataOrigin = (
            MetadataOrigin.TRINO if body.source_id == TRINO_SOURCE_ID else MetadataOrigin.NATIVE
        )
        items: list[AttachmentItem] = []
        table: ObservedTable
        for table in observed:
            items.append(
                self._attach_one(
                    scope_id=scope_id,
                    source=source,
                    catalog=body.catalog,
                    namespace=body.namespace,
                    table=table,
                    table_format=table_format,
                    origin=origin,
                )
            )
        return ScopeAttachmentResult(items=items)

    def _attach_one(
        self,
        *,
        scope_id: UUID,
        source: DataSourceRead,
        catalog: str,
        namespace: str,
        table: ObservedTable,
        table_format: str | None,
        origin: MetadataOrigin,
    ) -> AttachmentItem:
        source_key: str = source_key_from_parts(catalog, namespace, table.name)
        existing: CatalogTableRead | None = self._registry.find_table_by_source_key(
            source.id,
            source_key,
        )
        if existing is not None:
            outcome: AttachmentOutcome = (
                AttachmentOutcome.ALREADY_IN_SCOPE
                if existing.scope_id == scope_id
                else AttachmentOutcome.ALREADY_ELSEWHERE
            )
            return AttachmentItem(
                name=table.name,
                source_key=source_key,
                outcome=outcome,
                table_id=existing.id,
                scope_id=existing.scope_id,
            )
        kind: TableKind = TableKind.VIEW if table.kind == TableKind.VIEW.value else TableKind.TABLE
        created: CatalogTableRead = self._registry.create_table(
            scope_id,
            CatalogTableCreate(
                data_source_id=source.id,
                name=table.name,
                table_type=kind,
                physical_catalog=catalog,
                physical_namespace=namespace,
                physical_table=table.name,
                source_ref=SourceRef(catalog=catalog, namespace=namespace, table=table.name),
                source_key=source_key,
                table_format=table_format,
            ),
        )
        column: ObservedColumn
        for column in table.columns:
            self._registry.create_column(created.id, _column_body(column, origin))
        return AttachmentItem(
            name=table.name,
            source_key=source_key,
            outcome=AttachmentOutcome.CREATED,
            table_id=created.id,
            scope_id=created.scope_id,
        )

    def _data_source(self, source_id: str, catalog: str, connector: str) -> DataSourceRead:
        current: DataSourceRead
        for current in self._registry.list_data_sources():
            if _is_explorer_source(current.configuration, source_id, catalog):
                return current
        return self._registry.create_data_source(
            DataSourceCreate(
                name=_source_name(source_id, catalog),
                type=_source_type(source_id, connector),
                configuration={
                    "explorer_source": source_id,
                    "explorer_catalog": catalog,
                    "connector": connector,
                },
            )
        )


def _column_body(column: ObservedColumn, origin: MetadataOrigin) -> ColumnCreate:
    type_name: str = column.type_name
    if len(type_name) > _TYPE_LIMIT:
        type_name = type_name[:_TYPE_LIMIT]
    return ColumnCreate(
        name=column.name,
        data_type=type_name,
        discovered_data_type=type_name,
        metadata_origin=origin,
        source_description=column.comment,
        nullable=column.nullable,
        ordinal_position=column.ordinal_position,
    )


def _source_name(source_id: str, catalog: str) -> str:
    label: str = "Polaris" if source_id == POLARIS_SOURCE_ID else "Trino"
    name: str = f"{label} {catalog}"
    if len(name) <= 255:
        return name
    return name[:255]


def _source_type(source_id: str, connector: str) -> DataSourceType:
    if source_id == POLARIS_SOURCE_ID:
        return DataSourceType.ICEBERG_REST
    mapped: DataSourceType | None = _CONNECTOR_TYPES.get(connector.lower())
    if mapped is None:
        raise ValidationAppError(f"unsupported Trino connector: {connector}", field="catalog")
    return mapped


def _is_explorer_source(
    configuration: dict[str, JsonValue],
    source_id: str,
    catalog: str,
) -> bool:
    return (
        configuration.get("explorer_source") == source_id
        and configuration.get("explorer_catalog") == catalog
    )
