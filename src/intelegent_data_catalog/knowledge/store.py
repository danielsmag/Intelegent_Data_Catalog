"""Persist the latest built analysis context for one catalog table."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import ConflictError, PersistenceUnavailableError
from intelegent_data_catalog.knowledge.models import TableAnalysisContext
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import TableKnowledgeContextRow
from intelegent_data_catalog.registry.reads import dump_model

_CONFLICT: str = "knowledge context conflicts with an existing record"
_INVALID: str = "stored knowledge context is invalid"


class KnowledgeContextStore:
    """One snapshot per catalog table. A later build replaces the previous payload."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def save(self, context: TableAnalysisContext) -> None:
        """Insert or replace the snapshot for ``context.table.id``."""
        payload: dict[str, object] = dump_model(context)
        now: datetime = utc_now()
        row: TableKnowledgeContextRow | None = self._by_table(context.table.id)
        if row is None:
            created: TableKnowledgeContextRow = TableKnowledgeContextRow(
                id=uuid4(),
                table_id=context.table.id,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
        else:
            row.payload = payload
            row.updated_at = now
        self._commit()

    def get(self, table_id: UUID) -> TableAnalysisContext | None:
        """Return the saved snapshot, or none when this table has never been built."""
        row: TableKnowledgeContextRow | None = self._by_table(table_id)
        if row is None:
            return None
        return self._decode(row.payload, table_id)

    def _by_table(self, table_id: UUID) -> TableKnowledgeContextRow | None:
        statement: Select[tuple[TableKnowledgeContextRow]] = select(TableKnowledgeContextRow).where(
            TableKnowledgeContextRow.table_id == table_id
        )
        return self._session.scalar(statement)

    def _decode(self, payload: object, table_id: UUID) -> TableAnalysisContext:
        if not isinstance(payload, dict):
            raise PersistenceUnavailableError(_INVALID)
        try:
            context: TableAnalysisContext = TableAnalysisContext.model_validate(payload)
        except ValidationError as err:
            raise PersistenceUnavailableError(_INVALID) from err
        if context.table.id != table_id:
            raise PersistenceUnavailableError(_INVALID)
        return context

    def _commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as err:
            self._session.rollback()
            raise ConflictError(_CONFLICT) from err
