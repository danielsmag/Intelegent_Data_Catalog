"""Load table metadata from PostgreSQL. This provider does not profile data."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.knowledge.models import (
    ColumnMappingSummary,
    ColumnMetadata,
    ScopeSummary,
    SpaceSummary,
    TableCatalogContext,
    TableMetadata,
    TableRelation,
    TagSummary,
)
from intelegent_data_catalog.persistence.enums import (
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    TableKind,
    TableStatus,
)
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    ColumnRow,
    RelationshipColumnMappingRow,
    RelationshipRow,
    ScopeRow,
    SpaceRow,
    TagRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository


class CatalogContextProvider(Protocol):
    """Supplies canonical catalog metadata for one table id."""

    def get_table_context(self, table_id: UUID) -> TableCatalogContext:
        """Return the table, its columns, relationships, scope, space, and tags."""
        ...


class PostgresCatalogContextProvider:
    """Read one table's catalog context. Writes stay on the registry service."""

    def __init__(self, repository: RegistryRepository) -> None:
        self._repository: RegistryRepository = repository

    def get_table_context(self, table_id: UUID) -> TableCatalogContext:
        """Load metadata for ``table_id``. Missing rows raise ``EntityNotFoundError``."""
        table: CatalogTableRow | None = self._repository.get_table(table_id)
        if table is None:
            raise EntityNotFoundError("table", str(table_id))
        scope: ScopeRow | None = self._repository.get_scope(table.scope_id)
        if scope is None:
            raise EntityNotFoundError("scope", str(table.scope_id))
        space: SpaceRow | None = self._repository.get_space(scope.space_id)
        if space is None:
            raise EntityNotFoundError("space", str(scope.space_id))
        columns: list[ColumnRow] = self._repository.list_columns(table_id)
        relationship_rows: list[RelationshipRow] = self._repository.list_relationships_for_table(
            table_id
        )
        relationships: list[TableRelation] = [
            _relation(row, self._repository.list_mappings(row.id)) for row in relationship_rows
        ]
        foreign_keys: set[UUID] = _foreign_key_sources(relationships)
        return TableCatalogContext(
            table=TableMetadata(
                id=table.id,
                name=table.name,
                display_name=table.display_name,
                description=table.description,
                source_description=table.source_description,
                table_type=TableKind(table.table_type),
                status=TableStatus(table.status),
                data_source_id=table.data_source_id,
                scope_id=table.scope_id,
                space_id=space.id,
                physical_catalog=table.physical_catalog,
                physical_namespace=table.physical_namespace,
                physical_table=table.physical_table,
                source_key=table.source_key,
                row_count=table.row_count,
                context=table.context,
                scope=_scope_summary(scope),
                space=_space_summary(space),
                tags=_tags(self._repository, table_id),
            ),
            columns=[_column(row, foreign_keys) for row in columns],
            relationships=relationships,
        )


def _relation(
    row: RelationshipRow,
    mappings: list[RelationshipColumnMappingRow],
) -> TableRelation:
    return TableRelation(
        id=row.id,
        source_table_id=row.source_table_id,
        target_table_id=row.target_table_id,
        relationship_type=RelationshipType(row.relationship_type),
        origin=RelationshipOrigin(row.origin),
        status=RelationshipStatus(row.status),
        description=row.description,
        column_mappings=[
            ColumnMappingSummary(
                id=mapping.id,
                source_column_id=mapping.source_column_id,
                target_column_id=mapping.target_column_id,
                position=mapping.position,
            )
            for mapping in mappings
        ],
    )


def _foreign_key_sources(relationships: list[TableRelation]) -> set[UUID]:
    sources: set[UUID] = set()
    relation: TableRelation
    for relation in relationships:
        if relation.relationship_type != RelationshipType.FOREIGN_KEY:
            continue
        mapping: ColumnMappingSummary
        for mapping in relation.column_mappings:
            sources.add(mapping.source_column_id)
    return sources


def _column(row: ColumnRow, foreign_keys: set[UUID]) -> ColumnMetadata:
    return ColumnMetadata(
        id=row.id,
        table_id=row.table_id,
        name=row.name,
        description=row.description,
        source_description=row.source_description,
        data_type=row.data_type,
        native_data_type=row.native_data_type,
        discovered_data_type=row.discovered_data_type,
        nullable=row.nullable,
        ordinal_position=row.ordinal_position,
        is_primary_key=row.is_primary_key,
        is_unique=row.is_unique,
        is_foreign_key=row.id in foreign_keys,
        context=row.context,
    )


def _scope_summary(scope: ScopeRow) -> ScopeSummary:
    return ScopeSummary(
        id=scope.id,
        space_id=scope.space_id,
        name=scope.name,
        slug=scope.slug,
        description=scope.description,
        context=scope.context,
    )


def _space_summary(space: SpaceRow) -> SpaceSummary:
    return SpaceSummary(
        id=space.id,
        name=space.name,
        slug=space.slug,
        description=space.description,
        context=space.context,
    )


def _tags(repository: RegistryRepository, table_id: UUID) -> list[TagSummary]:
    tags: list[TagSummary] = []
    for attachment in repository.list_table_tags(table_id):
        tag: TagRow | None = repository.get_tag(attachment.tag_id)
        if tag is None:
            continue
        tags.append(TagSummary(id=tag.id, name=tag.name))
    tags.sort(key=lambda item: item.name)
    return tags
