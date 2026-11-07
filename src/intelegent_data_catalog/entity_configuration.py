"""Saved refresh interval and build options for a table or agent."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.strategies.flags import (
    DAG_FLAGS,
    FILE_FLAGS,
    INSTANCE_FLAGS,
    TABLE_FLAGS,
)
from intelegent_data_catalog.core.settings import (
    ModelId,
    ReasoningEffort,
    parse_effort,
    parse_model_id,
)
from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    AgentKind,
    AgentRefreshMode,
    CatalogJobKind,
    CatalogJobStatus,
    RefreshSubjectKind,
)
from intelegent_data_catalog.persistence.models import (
    AgentRow,
    CatalogJobRow,
    CatalogTableRow,
    EntityConfigurationRow,
)

_DEFAULT_INTERVAL_SECONDS: int = 600
_DEFAULTS: dict[RefreshSubjectKind, dict[str, bool]] = {
    RefreshSubjectKind.TABLE: TABLE_FLAGS,
}
_ASSET_DEFAULTS: dict[AgentKind, dict[str, bool]] = {
    AgentKind.TABLE: TABLE_FLAGS,
    AgentKind.AIRFLOW_DAG: DAG_FLAGS,
    AgentKind.AIRFLOW_INSTANCE: INSTANCE_FLAGS,
    AgentKind.NOTEBOOK: FILE_FLAGS,
    AgentKind.FILE: FILE_FLAGS,
    AgentKind.SCHEMA: TABLE_FLAGS,
}


class EntityConfigurationWrite(BaseModel):
    """Replace the saved refresh settings for one entity."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(ge=1, le=2_147_483_647)
    enabled: bool
    model: ModelId = "gpt-4.1"
    effort: ReasoningEffort = "low"
    refresh_mode: AgentRefreshMode = AgentRefreshMode.INTERVAL
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
    refresh_mode: AgentRefreshMode
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
        stored_kind, stored_id = self._stored_subject(kind, subject_id)
        row: EntityConfigurationRow | None = self._find(stored_kind, stored_id)
        if row is None:
            row = self._store_default(stored_kind, stored_id)
        return _read(
            kind,
            subject_id,
            row,
            next_rebuild_at(self._session, row),
            self._agent_kind(stored_kind, stored_id),
        )

    def put(
        self,
        kind: RefreshSubjectKind,
        subject_id: UUID,
        body: EntityConfigurationWrite,
    ) -> EntityConfigurationRead:
        """Create or replace the settings. The saved row is the schedule."""
        self._require(kind, subject_id)
        stored_kind, stored_id = self._stored_subject(kind, subject_id)
        options: dict[str, bool] = _checked_options(
            stored_kind,
            body.options,
            self._agent_kind(stored_kind, stored_id),
        )
        row: EntityConfigurationRow | None = self._find(stored_kind, stored_id)
        now: datetime = utc_now()
        if row is None:
            created: EntityConfigurationRow = self._new_row(
                stored_kind,
                stored_id,
                body.interval_seconds,
                body.enabled,
                body.model,
                body.effort,
                body.refresh_mode,
                options,
                now,
            )
            self._session.add(created)
            self._session.commit()
            return _read(
                kind,
                subject_id,
                created,
                next_rebuild_at(self._session, created),
                self._agent_kind(stored_kind, stored_id),
            )
        row.interval_seconds = body.interval_seconds
        row.enabled = body.enabled
        row.model = body.model
        row.effort = body.effort
        row.refresh_mode = body.refresh_mode.value
        row.options = dict(options)
        row.updated_at = now
        self._session.commit()
        return _read(
            kind,
            subject_id,
            row,
            next_rebuild_at(self._session, row),
            self._agent_kind(stored_kind, stored_id),
        )

    def _require(self, kind: RefreshSubjectKind, subject_id: UUID) -> None:
        if kind is RefreshSubjectKind.TABLE:
            if self._session.get(CatalogTableRow, subject_id) is None:
                raise EntityNotFoundError("catalog table", str(subject_id))
            return
        if self._session.get(AgentRow, subject_id) is None:
            raise EntityNotFoundError("agent", str(subject_id))

    def _stored_subject(
        self,
        kind: RefreshSubjectKind,
        subject_id: UUID,
    ) -> tuple[RefreshSubjectKind, UUID]:
        if kind is not RefreshSubjectKind.TABLE:
            return kind, subject_id
        table: CatalogTableRow | None = self._session.get(CatalogTableRow, subject_id)
        if table is None or table.agent_id is None:
            return kind, subject_id
        return RefreshSubjectKind.AGENT, table.agent_id

    def _agent_kind(self, kind: RefreshSubjectKind, subject_id: UUID) -> str | None:
        if kind is not RefreshSubjectKind.AGENT:
            return None
        agent: AgentRow | None = self._session.get(AgentRow, subject_id)
        if agent is None:
            return None
        return agent.kind

    def _find(self, kind: RefreshSubjectKind, subject_id: UUID) -> EntityConfigurationRow | None:
        if kind is RefreshSubjectKind.AGENT:
            statement = select(EntityConfigurationRow).where(
                EntityConfigurationRow.agent_id == subject_id,
            )
        else:
            statement = select(EntityConfigurationRow).where(
                EntityConfigurationRow.catalog_table_id == subject_id,
            )
        return self._session.scalar(statement)

    def saved_model(self, kind: RefreshSubjectKind, subject_id: UUID) -> ModelId:
        """Return the stored model id. A missing row uses the process default."""
        stored_kind, stored_id = self._stored_subject(kind, subject_id)
        row: EntityConfigurationRow | None = self._find(stored_kind, stored_id)
        if row is None:
            return self._default_model
        return parse_model_id(row.model)

    def saved_effort(self, kind: RefreshSubjectKind, subject_id: UUID) -> ReasoningEffort:
        """Return the stored effort. A missing row uses the process default."""
        stored_kind, stored_id = self._stored_subject(kind, subject_id)
        row: EntityConfigurationRow | None = self._find(stored_kind, stored_id)
        if row is None:
            return self._default_effort
        return parse_effort(row.effort)

    def _store_default(
        self,
        kind: RefreshSubjectKind,
        subject_id: UUID,
    ) -> EntityConfigurationRow:
        now: datetime = utc_now()
        created: EntityConfigurationRow = self._new_row(
            kind,
            subject_id,
            _DEFAULT_INTERVAL_SECONDS,
            True,
            self._default_model,
            self._default_effort,
            AgentRefreshMode.INTERVAL,
            self._default_options(kind, subject_id),
            now,
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

    def _default_options(self, kind: RefreshSubjectKind, subject_id: UUID) -> dict[str, bool]:
        if kind is RefreshSubjectKind.AGENT:
            agent_kind: str | None = self._agent_kind(kind, subject_id)
            if agent_kind is None:
                return dict(TABLE_FLAGS)
            return dict(_ASSET_DEFAULTS[AgentKind(agent_kind)])
        return dict(_DEFAULTS[kind])

    def _new_row(
        self,
        kind: RefreshSubjectKind,
        subject_id: UUID,
        interval_seconds: int,
        enabled: bool,
        model: ModelId,
        effort: ReasoningEffort,
        refresh_mode: AgentRefreshMode,
        options: dict[str, bool],
        now: datetime,
    ) -> EntityConfigurationRow:
        return EntityConfigurationRow(
            id=uuid4(),
            catalog_table_id=subject_id if kind is RefreshSubjectKind.TABLE else None,
            agent_id=subject_id if kind is RefreshSubjectKind.AGENT else None,
            refresh_mode=refresh_mode.value,
            interval_seconds=interval_seconds,
            enabled=enabled,
            model=model,
            effort=effort,
            options=dict(options),
            created_at=now,
            updated_at=now,
        )


def option_values(
    kind: RefreshSubjectKind,
    raw: dict[str, object],
    agent_kind: str | None = None,
) -> dict[str, bool]:
    """Merge stored flags with the defaults for this entity kind."""
    stored: dict[str, bool] = {}
    key: str
    value: object
    for key, value in raw.items():
        if isinstance(value, bool):
            stored[key] = value
    return _checked_options(kind, stored, agent_kind)


def next_rebuild_at(session: Session, row: EntityConfigurationRow) -> datetime | None:
    """Return when the next context rebuild is due. A disabled row has no deadline."""
    if not row.enabled or row.refresh_mode != AgentRefreshMode.INTERVAL.value:
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
    kind: CatalogJobKind | None
    subject_id: UUID | None
    kind, subject_id = _context_target(session, row)
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


def _context_target(
    session: Session,
    row: EntityConfigurationRow,
) -> tuple[CatalogJobKind | None, UUID | None]:
    """Job kind and subject this configuration rebuilds. A linked table uses its agent."""
    if row.agent_id is not None:
        return CatalogJobKind.AGENT, row.agent_id
    if row.catalog_table_id is not None:
        table: CatalogTableRow | None = session.get(CatalogTableRow, row.catalog_table_id)
        if table is not None and table.agent_id is not None:
            return CatalogJobKind.AGENT, table.agent_id
        return CatalogJobKind.KNOWLEDGE, row.catalog_table_id
    return None, None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _checked_options(
    kind: RefreshSubjectKind,
    options: dict[str, bool],
    agent_kind: str | None = None,
) -> dict[str, bool]:
    if kind is RefreshSubjectKind.AGENT:
        if agent_kind is None:
            allowed: dict[str, bool] = dict(TABLE_FLAGS)
        else:
            allowed = dict(_ASSET_DEFAULTS[AgentKind(agent_kind)])
    else:
        allowed = _DEFAULTS[kind]
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
    agent_kind: str | None = None,
) -> EntityConfigurationRead:
    option_kind: RefreshSubjectKind = (
        RefreshSubjectKind.AGENT if row.agent_id is not None else kind
    )
    return EntityConfigurationRead(
        subject_kind=kind,
        subject_id=subject_id,
        interval_seconds=row.interval_seconds,
        enabled=row.enabled,
        model=parse_model_id(row.model),
        effort=parse_effort(row.effort),
        refresh_mode=AgentRefreshMode(row.refresh_mode),
        options=option_values(option_kind, row.options, agent_kind),
        next_run_at=next_run_at,
    )
