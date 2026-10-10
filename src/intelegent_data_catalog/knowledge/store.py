"""Persist the latest built analysis context for one catalog table."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import (
    ConflictError,
    EntityNotFoundError,
    PersistenceUnavailableError,
)
from intelegent_data_catalog.knowledge.models import (
    ScopeAnalysisContext,
    SpaceAnalysisContext,
    TableAnalysisContext,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import ContextOrigin
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    ColumnRow,
    ScopeKnowledgeContextRow,
    ScopeRow,
    SpaceKnowledgeContextRow,
    SpaceRow,
    TableKnowledgeContextRow,
)
from intelegent_data_catalog.registry.reads import dump_model

_CONFLICT: str = "knowledge context conflicts with an existing record"
_INVALID: str = "stored knowledge context is invalid"
_INVALID_SCOPE: str = "stored scope knowledge context is invalid"
_INVALID_SPACE: str = "stored space knowledge context is invalid"


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


class SavedSpaceContext:
    """One stored space snapshot plus the time it was written."""

    def __init__(self, context: SpaceAnalysisContext, built_at: datetime) -> None:
        self.context: SpaceAnalysisContext = context
        self.built_at: datetime = built_at


class SpaceKnowledgeContextStore:
    """One snapshot per catalog space. A later build replaces the previous payload."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def save(self, context: SpaceAnalysisContext) -> None:
        """Insert or replace the snapshot for ``context.space.id``."""
        payload: dict[str, object] = dump_model(context)
        now: datetime = utc_now()
        row: SpaceKnowledgeContextRow | None = self._by_space(context.space.id)
        if row is None:
            created: SpaceKnowledgeContextRow = SpaceKnowledgeContextRow(
                id=uuid4(),
                space_id=context.space.id,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
        else:
            row.payload = payload
            row.updated_at = now
        self._commit()

    def get(self, space_id: UUID) -> SpaceAnalysisContext | None:
        """Return the saved snapshot, or none when this space has never been built."""
        saved: SavedSpaceContext | None = self.saved(space_id)
        if saved is None:
            return None
        return saved.context

    def saved(self, space_id: UUID) -> SavedSpaceContext | None:
        """Return the snapshot and its write time."""
        row: SpaceKnowledgeContextRow | None = self._by_space(space_id)
        if row is None:
            return None
        return SavedSpaceContext(self._decode(row.payload, space_id), row.updated_at)

    def _by_space(self, space_id: UUID) -> SpaceKnowledgeContextRow | None:
        statement: Select[tuple[SpaceKnowledgeContextRow]] = select(SpaceKnowledgeContextRow).where(
            SpaceKnowledgeContextRow.space_id == space_id
        )
        return self._session.scalar(statement)

    def _decode(self, payload: object, space_id: UUID) -> SpaceAnalysisContext:
        if not isinstance(payload, dict):
            raise PersistenceUnavailableError(_INVALID_SPACE)
        try:
            context: SpaceAnalysisContext = SpaceAnalysisContext.model_validate(payload)
        except ValidationError as err:
            raise PersistenceUnavailableError(_INVALID_SPACE) from err
        if context.space.id != space_id:
            raise PersistenceUnavailableError(_INVALID_SPACE)
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


class SemanticContextStore:
    """Writes business context onto a space, scope, table, or column row."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def save_space(self, space_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the space context. The row must already exist."""
        row: SpaceRow | None = self._session.get(SpaceRow, space_id)
        if row is None:
            raise EntityNotFoundError("space", str(space_id))
        self._write(row, text, origin)

    def save_scope(self, scope_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the scope context. The row must already exist."""
        row: ScopeRow | None = self._session.get(ScopeRow, scope_id)
        if row is None:
            raise EntityNotFoundError("scope", str(scope_id))
        self._write(row, text, origin)

    def save_table(self, table_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the table context. The row must already exist."""
        row: CatalogTableRow | None = self._session.get(CatalogTableRow, table_id)
        if row is None:
            raise EntityNotFoundError("table", str(table_id))
        self._write(row, text, origin)

    def save_column(self, column_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the column context. The row must already exist."""
        row: ColumnRow | None = self._session.get(ColumnRow, column_id)
        if row is None:
            raise EntityNotFoundError("column", str(column_id))
        self._write(row, text, origin)

    def _write(
        self,
        row: SpaceRow | ScopeRow | CatalogTableRow | ColumnRow,
        text: str,
        origin: ContextOrigin,
    ) -> None:
        row.context = text
        row.context_origin = origin.value
        row.updated_at = utc_now()
        self._commit()

    def _commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as err:
            self._session.rollback()
            raise ConflictError(_CONFLICT) from err
