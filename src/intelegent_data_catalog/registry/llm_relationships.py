"""Replace relationships the scope agent proposed. Other origins stay in place."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from intelegent_data_catalog.knowledge.models import TableLink, TableLinkKind
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from intelegent_data_catalog.persistence.models import RelationshipColumnMappingRow, RelationshipRow
from intelegent_data_catalog.registry.repository import RegistryRepository


class LlmRelationshipStore:
    """Writes ``origin=LLM`` relationships for one scope and drops the previous set."""

    def __init__(self, session: Session) -> None:
        self._repository: RegistryRepository = RegistryRepository(session)

    def replace(self, scope_table_ids: set[UUID], links: list[TableLink]) -> None:
        """Delete prior LLM rows whose tables are in the scope, then insert ``links``."""
        existing: list[RelationshipRow] = self._repository.list_relationships_between(
            scope_table_ids
        )
        row: RelationshipRow
        for row in existing:
            if row.origin != RelationshipOrigin.LLM.value:
                continue
            self._repository.delete_mappings(row.id)
            self._repository.delete(row)
        link: TableLink
        for link in links:
            relationship_type: RelationshipType = (
                RelationshipType.FOREIGN_KEY
                if link.kind == TableLinkKind.REFERENCES
                else RelationshipType.MANUAL
            )
            now: datetime = utc_now()
            created: RelationshipRow = self._repository.add_relationship(
                RelationshipRow(
                    id=uuid4(),
                    source_table_id=link.source_table_id,
                    target_table_id=link.target_table_id,
                    relationship_type=relationship_type.value,
                    origin=RelationshipOrigin.LLM.value,
                    status=RelationshipStatus.UNVERIFIED.value,
                    description=link.reason,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._repository.add_mapping(
                RelationshipColumnMappingRow(
                    id=uuid4(),
                    relationship_id=created.id,
                    source_column_id=link.source_column_id,
                    target_column_id=link.target_column_id,
                    position=0,
                )
            )
        self._repository.commit()
