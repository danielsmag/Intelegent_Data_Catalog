"""Saved refresh interval and build options for a space, scope, or table."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.core.settings import (
    ModelId,
    ReasoningEffort,
    parse_effort,
    parse_model_id,
)
from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    CatalogJobKind,
    CatalogJobStatus,
    RefreshSubjectKind,
)
from intelegent_data_catalog.persistence.models import (
    CatalogJobRow,
    CatalogTableRow,
    EntityConfigurationRow,
    ScopeRow,
    SpaceRow,
)

_DEFAULT_INTERVAL_SECONDS: int = 600
_SPACE_OPTIONS: dict[str, bool] = {
    "include_scope_knowledge": True,
    "include_previous": True,
    "include_summary": True,
    "include_airflow": True,
}
_SCOPE_OPTIONS: dict[str, bool] = {
    "include_table_knowledge": True,
    "include_previous": True,
    "include_summary": True,
    "include_airflow": True,
}
_TABLE_OPTIONS: dict[str, bool] = {
    "include_graph": True,
    "include_lineage": True,
    "include_data_profile": False,
}
_DEFAULTS: dict[RefreshSubjectKind, dict[str, bool]] = {
    RefreshSubjectKind.SPACE: _SPACE_OPTIONS,
    RefreshSubjectKind.SCOPE: _SCOPE_OPTIONS,
    RefreshSubjectKind.TABLE: _TABLE_OPTIONS,
}


class EntityConfigurationWrite(BaseModel):
    """Replace the saved refresh settings for one entity."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(ge=1, le=2_147_483_647)
    enabled: bool
    model: ModelId = "gpt-4.1"
    effort: ReasoningEffort = "low"
    options: dict[str, bool]


class EntityConfigurationRead(BaseModel):
    """Effective refresh settings. ``next_run_at`` is when Beat starts the next rebuild."""

    model_config = ConfigDict(extra="forbid")

    subject_kind: RefreshSubjectKind
    subject_id: UUID
    interval_seconds: int
    enabled: bool
    model: ModelId
    effort: ReasoningEffort
    options: dict[str, bool]
    next_run_at: datetime | None


class EntityConfigurationService:
    """Reads and writes entity configuration. The caller owns the session."""

    def __init__(
        self,
        session: Session,
        default_model: ModelId = "llama3.2",
        default_effort: ReasoningEffort = "low",
    ) -> None:
        self._session: Session = session
        self._default_model: ModelId = default_model
        self._default_effort: ReasoningEffort = default_effort

    def get(self, kind: RefreshSubjectKind, subject_id: UUID) -> EntityConfigurationRead:
        """Return the saved settings. The first read stores the default schedule."""
        self._require(kind, subject_id)
        row: EntityConfigurationRow | None = self._find(kind, subject_id)
        if row is None:
            row = self._store_default(kind, subject_id)
        return _read(kind, subject_id, row, next_rebuild_at(self._session, row))

    def put(
        self,
        kind: RefreshSubjectKind,
        subject_id: UUID,
        body: EntityConfigurationWrite,
    ) -> EntityConfigurationRead:
        """Create or replace the settings. The saved row is the schedule."""
        self._require(kind, subject_id)
        options: dict[str, bool] = _checked_options(kind, body.options)
        row: EntityConfigurationRow | None = self._find(kind, subject_id)
        now: datetime = utc_now()
        if row is None:
            created: EntityConfigurationRow = EntityConfigurationRow(
                id=uuid4(),
                space_id=subject_id if kind is RefreshSubjectKind.SPACE else None,
                scope_id=subject_id if kind is RefreshSubjectKind.SCOPE else None,
                catalog_table_id=subject_id if kind is RefreshSubjectKind.TABLE else None,
                interval_seconds=body.interval_seconds,
                enabled=body.enabled,
                model=body.model,
                effort=body.effort,
                options=dict(options),
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
            self._session.commit()
            return _read(kind, subject_id, created, next_rebuild_at(self._session, created))
        row.interval_seconds = body.interval_seconds
        row.enabled = body.enabled
        row.model = body.model
        row.effort = body.effort
        row.options = dict(options)
        row.updated_at = now
        self._session.commit()
        return _read(kind, subject_id, row, next_rebuild_at(self._session, row))

    def _require(self, kind: RefreshSubjectKind, subject_id: UUID) -> None:
        if kind is RefreshSubjectKind.SPACE:
            if self._session.get(SpaceRow, subject_id) is None:
                raise EntityNotFoundError("space", str(subject_id))
            return
        if kind is RefreshSubjectKind.SCOPE:
            if self._session.get(ScopeRow, subject_id) is None:
                raise EntityNotFoundError("scope", str(subject_id))
            return
        if self._session.get(CatalogTableRow, subject_id) is None:
            raise EntityNotFoundError("catalog table", str(subject_id))

    def _find(self, kind: RefreshSubjectKind, subject_id: UUID) -> EntityConfigurationRow | None:
        if kind is RefreshSubjectKind.SPACE:
            statement = select(EntityConfigurationRow).where(
                EntityConfigurationRow.space_id == subject_id,
            )
        elif kind is RefreshSubjectKind.SCOPE:
            statement = select(EntityConfigurationRow).where(
                EntityConfigurationRow.scope_id == subject_id,
            )
        else:
            statement = select(EntityConfigurationRow).where(
                EntityConfigurationRow.catalog_table_id == subject_id,
            )
        return self._session.scalar(statement)

    def saved_model(self, kind: RefreshSubjectKind, subject_id: UUID) -> ModelId:
        """Return the stored model id. A missing row uses the process default."""
        row: EntityConfigurationRow | None = self._find(kind, subject_id)
        if row is None:
            return self._default_model
        return parse_model_id(row.model)

    def saved_effort(self, kind: RefreshSubjectKind, subject_id: UUID) -> ReasoningEffort:
        """Return the stored effort. A missing row uses the process default."""
        row: EntityConfigurationRow | None = self._find(kind, subject_id)
        if row is None:
            return self._default_effort
        return parse_effort(row.effort)

    def _store_default(
        self,
        kind: RefreshSubjectKind,
        subject_id: UUID,
    ) -> EntityConfigurationRow:
        now: datetime = utc_now()
        created: EntityConfigurationRow = EntityConfigurationRow(
            id=uuid4(),
            space_id=subject_id if kind is RefreshSubjectKind.SPACE else None,
            scope_id=subject_id if kind is RefreshSubjectKind.SCOPE else None,
            catalog_table_id=subject_id if kind is RefreshSubjectKind.TABLE else None,
            interval_seconds=_DEFAULT_INTERVAL_SECONDS,
            enabled=True,
            model=self._default_model,
            effort=self._default_effort,
            options=dict(_DEFAULTS[kind]),
            created_at=now,
            updated_at=now,
        )
        self._session.add(created)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            raced: EntityConfigurationRow | None = self._find(kind, subject_id)
            if raced is None:
                raise
            return raced
        return created


def option_values(kind: RefreshSubjectKind, raw: dict[str, object]) -> dict[str, bool]:
    """Merge stored flags with the defaults for this entity kind."""
    stored: dict[str, bool] = {}
    key: str
    value: object
    for key, value in raw.items():
        if isinstance(value, bool):
            stored[key] = value
    return _checked_options(kind, stored)


def next_rebuild_at(session: Session, row: EntityConfigurationRow) -> datetime | None:
    """Return when the next context rebuild is due. A disabled row has no deadline."""
    if not row.enabled:
        return None
    latest: CatalogJobRow | None = _latest_context_job(session, row)
    anchor: datetime = row.created_at if latest is None else latest.created_at
    return _aware(anchor) + timedelta(seconds=row.interval_seconds)


def context_rebuild_running(session: Session, row: EntityConfigurationRow) -> bool:
    """Return whether this configuration's context rebuild is still running."""
    latest: CatalogJobRow | None = _latest_context_job(session, row)
    if latest is None:
        return False
    return latest.status == CatalogJobStatus.RUNNING.value


def _latest_context_job(session: Session, row: EntityConfigurationRow) -> CatalogJobRow | None:
    kind: CatalogJobKind | None = _context_job_kind(row)
    subject_id: UUID | None = _configuration_subject_id(row)
    if kind is None or subject_id is None:
        return None
    statement = (
        select(CatalogJobRow)
        .where(
            CatalogJobRow.kind == kind.value,
            CatalogJobRow.subject_id == subject_id,
            CatalogJobRow.lane == "",
        )
        .order_by(CatalogJobRow.created_at.desc(), CatalogJobRow.id.desc())
        .limit(1)
    )
    return session.scalar(statement)


def _context_job_kind(row: EntityConfigurationRow) -> CatalogJobKind | None:
    if row.catalog_table_id is not None:
        return CatalogJobKind.KNOWLEDGE
    if row.scope_id is not None:
        return CatalogJobKind.SCOPE
    if row.space_id is not None:
        return CatalogJobKind.SPACE
    return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _configuration_subject_id(row: EntityConfigurationRow) -> UUID | None:
    if row.catalog_table_id is not None:
        return row.catalog_table_id
    if row.scope_id is not None:
        return row.scope_id
    return row.space_id


def _checked_options(kind: RefreshSubjectKind, options: dict[str, bool]) -> dict[str, bool]:
    allowed: dict[str, bool] = _DEFAULTS[kind]
    unknown: list[str] = sorted(set(options) - set(allowed))
    if len(unknown) > 0:
        names: str = ", ".join(unknown)
        raise ValidationAppError(
            f"unknown configuration option: {names}",
            field="options",
        )
    merged: dict[str, bool] = dict(allowed)
    merged.update(options)
    return merged


def _read(
    kind: RefreshSubjectKind,
    subject_id: UUID,
    row: EntityConfigurationRow,
    next_run_at: datetime | None,
) -> EntityConfigurationRead:
    return EntityConfigurationRead(
        subject_kind=kind,
        subject_id=subject_id,
        interval_seconds=row.interval_seconds,
        enabled=row.enabled,
        model=parse_model_id(row.model),
        effort=parse_effort(row.effort),
        options=option_values(kind, row.options),
        next_run_at=next_run_at,
    )
