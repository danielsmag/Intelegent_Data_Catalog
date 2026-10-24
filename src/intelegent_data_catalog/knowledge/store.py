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
    AirflowAnalysisContext,
    DagFinding,
    ScopeAnalysisContext,
    SpaceAnalysisContext,
    TableAnalysisContext,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import ContextOrigin
from intelegent_data_catalog.persistence.models import (
    AirflowInstanceKnowledgeContextRow,
    AirflowInstanceRow,
    AssetKnowledgeContextRow,
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
_INVALID_AIRFLOW: str = "stored airflow knowledge context is invalid"


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
        """Return the asset snapshot when this table has one, otherwise the table row."""
        stored: TableAnalysisContext | None = self._from_asset(table_id)
        if stored is not None:
            return stored
        row: TableKnowledgeContextRow | None = self._by_table(table_id)
        if row is None:
            return None
        return self._decode(row.payload, table_id)

    def _from_asset(self, table_id: UUID) -> TableAnalysisContext | None:
        """Read a table snapshot nested in the linked asset. A thin payload is absent."""
        table: CatalogTableRow | None = self._session.get(CatalogTableRow, table_id)
        if table is None or table.asset_id is None:
            return None
        statement: Select[tuple[AssetKnowledgeContextRow]] = select(AssetKnowledgeContextRow).where(
            AssetKnowledgeContextRow.asset_id == table.asset_id,
        )
        saved: AssetKnowledgeContextRow | None = self._session.scalar(statement)
        if saved is None or not isinstance(saved.payload, dict):
            return None
        inner: object = saved.payload.get("payload")
        if not isinstance(inner, dict):
            return None
        try:
            context: TableAnalysisContext = TableAnalysisContext.model_validate(inner)
        except ValidationError:
            return None
        if context.table.id != table_id:
            return None
        return context

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


class SavedAirflowContext:
    """One stored Airflow snapshot plus the time it was written."""

    def __init__(self, context: AirflowAnalysisContext, built_at: datetime) -> None:
        self.context: AirflowAnalysisContext = context
        self.built_at: datetime = built_at


class AirflowKnowledgeContextStore:
    """One snapshot per Airflow instance. Findings for untouched DAGs stay in place."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def save(self, context: AirflowAnalysisContext) -> None:
        """Insert or replace the snapshot for ``context.instance_id``."""
        self._upsert(context)
        self._commit()

    def merge_findings(self, context: AirflowAnalysisContext) -> AirflowAnalysisContext:
        """Upsert findings for the analyzed DAGs. Other DAGs and a prior summary stay."""
        existing: AirflowAnalysisContext | None = self.get(context.instance_id)
        if existing is None:
            self.save(context)
            return context
        by_dag: dict[UUID, DagFinding] = {item.dag_row_id: item for item in existing.findings}
        finding: DagFinding
        for finding in context.findings:
            by_dag[finding.dag_row_id] = finding
        summary: str | None = existing.summary if existing.summary else context.summary
        merged: AirflowAnalysisContext = AirflowAnalysisContext(
            instance_id=context.instance_id,
            instance_name=context.instance_name,
            summary=summary,
            findings=list(by_dag.values()),
            dags_analyzed=len(by_dag),
            previous_links_kept=context.previous_links_kept,
        )
        self.save(merged)
        return merged

    def get(self, instance_id: UUID) -> AirflowAnalysisContext | None:
        """Return the saved snapshot, or none when this instance has never been built."""
        saved: SavedAirflowContext | None = self.saved(instance_id)
        if saved is None:
            return None
        return saved.context

    def saved(self, instance_id: UUID) -> SavedAirflowContext | None:
        """Return the snapshot and its write time."""
        row: AirflowInstanceKnowledgeContextRow | None = self._by_instance(instance_id)
        if row is None:
            return None
        return SavedAirflowContext(self._decode(row.payload, instance_id), row.updated_at)

    def _upsert(self, context: AirflowAnalysisContext) -> None:
        payload: dict[str, object] = dump_model(context)
        now: datetime = utc_now()
        row: AirflowInstanceKnowledgeContextRow | None = self._by_instance(context.instance_id)
        if row is None:
            created: AirflowInstanceKnowledgeContextRow = AirflowInstanceKnowledgeContextRow(
                id=uuid4(),
                instance_id=context.instance_id,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
            return
        row.payload = payload
        row.updated_at = now

    def _by_instance(self, instance_id: UUID) -> AirflowInstanceKnowledgeContextRow | None:
        statement: Select[tuple[AirflowInstanceKnowledgeContextRow]] = select(
            AirflowInstanceKnowledgeContextRow,
        ).where(AirflowInstanceKnowledgeContextRow.instance_id == instance_id)
        return self._session.scalar(statement)

    def _decode(self, payload: object, instance_id: UUID) -> AirflowAnalysisContext:
        if not isinstance(payload, dict):
            raise PersistenceUnavailableError(_INVALID_AIRFLOW)
        try:
            context: AirflowAnalysisContext = AirflowAnalysisContext.model_validate(payload)
        except ValidationError as err:
            raise PersistenceUnavailableError(_INVALID_AIRFLOW) from err
        if context.instance_id != instance_id:
            raise PersistenceUnavailableError(_INVALID_AIRFLOW)
        return context

    def _commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as err:
            self._session.rollback()
            raise ConflictError(_CONFLICT) from err


class SemanticContextStore:
    """Writes business context onto a space, scope, table, column, or Airflow instance."""

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

    def save_airflow_instance(self, instance_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the Airflow instance context. The row must already exist."""
        row: AirflowInstanceRow | None = self._session.get(AirflowInstanceRow, instance_id)
        if row is None:
            raise EntityNotFoundError("airflow instance", str(instance_id))
        self._write(row, text, origin)

    def _write(
        self,
        row: SpaceRow | ScopeRow | CatalogTableRow | ColumnRow | AirflowInstanceRow,
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
