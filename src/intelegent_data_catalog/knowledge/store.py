"""Persist the latest built analysis context for one catalog table."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import ConflictError, PersistenceUnavailableError
from intelegent_data_catalog.knowledge.models import ScopeAnalysisContext, TableAnalysisContext
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import (
    ScopeKnowledgeContextRow,
    TableKnowledgeContextRow,
)
from intelegent_data_catalog.registry.reads import dump_model

_CONFLICT: str = "knowledge context conflicts with an existing record"
_INVALID: str = "stored knowledge context is invalid"
_INVALID_SCOPE: str = "stored scope knowledge context is invalid"


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


class SavedScopeContext:
    """One stored scope snapshot plus the time it was written."""

    def __init__(self, context: ScopeAnalysisContext, built_at: datetime) -> None:
        self.context: ScopeAnalysisContext = context
        self.built_at: datetime = built_at


class ScopeKnowledgeContextStore:
    """One snapshot per catalog scope. A later build replaces the previous payload."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def save(self, context: ScopeAnalysisContext) -> None:
        """Insert or replace the snapshot for ``context.scope.id``."""
        payload: dict[str, object] = dump_model(context)
        now: datetime = utc_now()
        row: ScopeKnowledgeContextRow | None = self._by_scope(context.scope.id)
        if row is None:
            created: ScopeKnowledgeContextRow = ScopeKnowledgeContextRow(
                id=uuid4(),
                scope_id=context.scope.id,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
        else:
            row.payload = payload
            row.updated_at = now
        self._commit()

    def get(self, scope_id: UUID) -> ScopeAnalysisContext | None:
        """Return the saved snapshot, or none when this scope has never been built."""
        saved: SavedScopeContext | None = self.saved(scope_id)
        if saved is None:
            return None
        return saved.context

    def saved(self, scope_id: UUID) -> SavedScopeContext | None:
        """Return the snapshot and its write time."""
        row: ScopeKnowledgeContextRow | None = self._by_scope(scope_id)
        if row is None:
            return None
        return SavedScopeContext(self._decode(row.payload, scope_id), row.updated_at)

    def _by_scope(self, scope_id: UUID) -> ScopeKnowledgeContextRow | None:
        statement: Select[tuple[ScopeKnowledgeContextRow]] = select(ScopeKnowledgeContextRow).where(
            ScopeKnowledgeContextRow.scope_id == scope_id
        )
        return self._session.scalar(statement)

    def _decode(self, payload: object, scope_id: UUID) -> ScopeAnalysisContext:
        if not isinstance(payload, dict):
            raise PersistenceUnavailableError(_INVALID_SCOPE)
        try:
            context: ScopeAnalysisContext = ScopeAnalysisContext.model_validate(payload)
        except ValidationError as err:
            raise PersistenceUnavailableError(_INVALID_SCOPE) from err
        if context.scope.id != scope_id:
            raise PersistenceUnavailableError(_INVALID_SCOPE)
        return context

    def _commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as err:
            self._session.rollback()
            raise ConflictError(_CONFLICT) from err
