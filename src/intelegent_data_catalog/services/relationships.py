"""Relationship writes and reads."""

from datetime import datetime
from uuid import UUID, uuid4

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    ColumnRow,
    RelationshipColumnMappingRow,
    RelationshipRow,
)
from intelegent_data_catalog.registry.reads import relationship_read
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    ColumnMappingWrite,
    RelationshipCreate,
    RelationshipRead,
    RelationshipUpdate,
    TableRelationships,
)
from intelegent_data_catalog.services.support import require_row, required


class RelationshipService:
    """Relationship persistence against the shared catalog session."""

    def __init__(self, repo: RegistryRepository) -> None:
        self._repo: RegistryRepository = repo

    def create_relationship(self, body: RelationshipCreate) -> RelationshipRead:
        """Create a manual or imported relationship with composite mappings."""
        self._table(body.source_table_id)
        self._table(body.target_table_id)
        positions: list[int] = _positions(body.column_mappings)
        self._validate_mappings(body.source_table_id, body.target_table_id, body.column_mappings)
        now: datetime = utc_now()
        row: RelationshipRow = RelationshipRow(
            id=uuid4(),
            source_table_id=body.source_table_id,
            target_table_id=body.target_table_id,
            relationship_type=body.relationship_type.value,
            origin=body.origin.value,
            status=body.status.value,
            description=body.description,
            created_at=now,
            updated_at=now,
        )
        stored: RelationshipRow = self._repo.add_relationship(row)
        self._insert_mappings(stored.id, body.column_mappings, positions)
        self._repo.commit()
        return self._relationship_read(stored)

    def get_relationship(self, relationship_id: UUID) -> RelationshipRead:
        """Return one relationship."""
        return self._relationship_read(self._relationship(relationship_id))

    def update_relationship(
        self,
        relationship_id: UUID,
        patch: RelationshipUpdate,
    ) -> RelationshipRead:
        """Patch a relationship. A mapping list replaces the previous pairs."""
        row: RelationshipRow = self._relationship(relationship_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "relationship_type" in changes:
            row.relationship_type = required(patch.relationship_type, "relationship_type").value
        if "origin" in changes:
            row.origin = required(patch.origin, "origin").value
        if "status" in changes:
            row.status = required(patch.status, "status").value
        if "description" in changes:
            row.description = patch.description
        if "column_mappings" in changes:
            mappings: list[ColumnMappingWrite] = required(patch.column_mappings, "column_mappings")
            if len(mappings) == 0:
                raise ValidationAppError(
                    "column_mappings must contain at least one pair",
                    field="column_mappings",
                )
            positions: list[int] = _positions(mappings)
            self._validate_mappings(row.source_table_id, row.target_table_id, mappings)
            self._repo.delete_mappings(row.id)
            self._insert_mappings(row.id, mappings, positions)
        row.updated_at = utc_now()
        self._repo.commit()
        return self._relationship_read(row)

    def delete_relationship(self, relationship_id: UUID) -> None:
        """Delete a relationship and its column mappings."""
        row: RelationshipRow = self._relationship(relationship_id)
        self._repo.delete_mappings(relationship_id)
        self._repo.delete(row)
        self._repo.commit()

    def list_table_relationships(self, table_id: UUID) -> TableRelationships:
        """Return incoming and outgoing relationships for a table."""
        self._table(table_id)
        incoming: list[RelationshipRead] = []
        outgoing: list[RelationshipRead] = []
        row: RelationshipRow
        for row in self._repo.list_relationships_for_table(table_id):
            read: RelationshipRead = self._relationship_read(row)
            if row.source_table_id == table_id:
                outgoing.append(read)
            if row.target_table_id == table_id and row.source_table_id != table_id:
                incoming.append(read)
        return TableRelationships(incoming=incoming, outgoing=outgoing)

    def _validate_mappings(
        self,
        source_table_id: UUID,
        target_table_id: UUID,
        mappings: list[ColumnMappingWrite],
    ) -> None:
        seen: set[tuple[UUID, UUID]] = set()
        item: ColumnMappingWrite
        for item in mappings:
            pair: tuple[UUID, UUID] = (item.source_column_id, item.target_column_id)
            if pair in seen:
                raise ValidationAppError(
                    "column mappings must be unique",
                    field="column_mappings",
                )
            seen.add(pair)
            source_column: ColumnRow = self._column(item.source_column_id)
            target_column: ColumnRow = self._column(item.target_column_id)
            if source_column.table_id != source_table_id:
                raise ValidationAppError(
                    "source column does not belong to the source table",
                    field="column_mappings",
                )
            if target_column.table_id != target_table_id:
                raise ValidationAppError(
                    "target column does not belong to the target table",
                    field="column_mappings",
                )

    def _insert_mappings(
        self,
        relationship_id: UUID,
        mappings: list[ColumnMappingWrite],
        positions: list[int],
    ) -> None:
        index: int
        item: ColumnMappingWrite
        for index, item in enumerate(mappings):
            mapping: RelationshipColumnMappingRow = RelationshipColumnMappingRow(
                id=uuid4(),
                relationship_id=relationship_id,
                source_column_id=item.source_column_id,
                target_column_id=item.target_column_id,
                position=positions[index],
            )
            self._repo.add_mapping(mapping)

    def _relationship_read(self, row: RelationshipRow) -> RelationshipRead:
        mappings: list[RelationshipColumnMappingRow] = self._repo.list_mappings(row.id)
        names: dict[UUID, str] = {}
        mapping: RelationshipColumnMappingRow
        for mapping in mappings:
            source_column: ColumnRow = self._column(mapping.source_column_id)
            target_column: ColumnRow = self._column(mapping.target_column_id)
            names[source_column.id] = source_column.name
            names[target_column.id] = target_column.name
        return relationship_read(row, mappings, names)

    def _table(self, table_id: UUID) -> CatalogTableRow:
        row: CatalogTableRow | None = self._repo.get_table(table_id)
        return require_row(row, "table", table_id)

    def _column(self, column_id: UUID) -> ColumnRow:
        row: ColumnRow | None = self._repo.get_column(column_id)
        return require_row(row, "column", column_id)

    def _relationship(self, relationship_id: UUID) -> RelationshipRow:
        row: RelationshipRow | None = self._repo.get_relationship(relationship_id)
        return require_row(row, "relationship", relationship_id)


def _positions(mappings: list[ColumnMappingWrite]) -> list[int]:
    omitted: bool = any(item.position is None for item in mappings)
    provided: bool = any(item.position is not None for item in mappings)
    if omitted and provided:
        raise ValidationAppError(
            "column mapping positions must all be set or all omitted",
            field="column_mappings",
        )
    if omitted:
        return list(range(len(mappings)))
    positions: list[int] = []
    item: ColumnMappingWrite
    for item in mappings:
        if item.position is None:
            raise ValidationAppError(
                "column mapping positions must all be set or all omitted",
                field="column_mappings",
            )
        positions.append(item.position)
    if len(set(positions)) != len(positions):
        raise ValidationAppError(
            "column mapping positions must be unique",
            field="column_mappings",
        )
    return positions
