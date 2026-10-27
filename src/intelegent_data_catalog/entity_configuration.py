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
    AssetKind,
    AssetRefreshMode,
    CatalogJobKind,
    CatalogJobStatus,
    RefreshSubjectKind,
)
from intelegent_data_catalog.persistence.models import (
    AssetRow,
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
_DAG_OPTIONS: dict[str, bool] = {
    "include_source": True,
    "include_previous": True,
}
_INSTANCE_OPTIONS: dict[str, bool] = {
    "include_source": True,
    "include_previous": True,
}
_FILE_OPTIONS: dict[str, bool] = {
    "include_outputs": False,
    "include_table_links": True,
}
_DEFAULTS: dict[RefreshSubjectKind, dict[str, bool]] = {
    RefreshSubjectKind.SPACE: _SPACE_OPTIONS,
    RefreshSubjectKind.SCOPE: _SCOPE_OPTIONS,
    RefreshSubjectKind.TABLE: _TABLE_OPTIONS,
}
_ASSET_DEFAULTS: dict[AssetKind, dict[str, bool]] = {
    AssetKind.TABLE: _TABLE_OPTIONS,
    AssetKind.AIRFLOW_DAG: _DAG_OPTIONS,
    AssetKind.AIRFLOW_INSTANCE: _INSTANCE_OPTIONS,
    AssetKind.NOTEBOOK: _FILE_OPTIONS,
    AssetKind.FILE: _FILE_OPTIONS,
}


class EntityConfigurationWrite(BaseModel):
    """Replace the saved refresh settings for one entity."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(ge=1, le=2_147_483_647)
    enabled: bool
    model: ModelId = "gpt-4.1"
    effort: ReasoningEffort = "low"
    refresh_mode: AssetRefreshMode = AssetRefreshMode.INTERVAL
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
    refresh_mode: AssetRefreshMode
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
            self._asset_kind(stored_kind, stored_id),
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
            self._asset_kind(stored_kind, stored_id),
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
                self._asset_kind(stored_kind, stored_id),
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
            self._asset_kind(stored_kind, stored_id),
        )

    def _require(self, kind: RefreshSubjectKind, subject_id: UUID) -> None:
        if kind is RefreshSubjectKind.SPACE:
            if self._session.get(SpaceRow, subject_id) is None:
                raise EntityNotFoundError("space", str(subject_id))
            return
        if kind is RefreshSubjectKind.SCOPE:
            if self._session.get(ScopeRow, subject_id) is None:
                raise EntityNotFoundError("scope", str(subject_id))
            return
        if kind is RefreshSubjectKind.TABLE:
            if self._session.get(CatalogTableRow, subject_id) is None:
                raise EntityNotFoundError("catalog table", str(subject_id))
            return
        if self._session.get(AssetRow, subject_id) is None:
            raise EntityNotFoundError("asset", str(subject_id))

    def _stored_subject(
        self,
        kind: RefreshSubjectKind,
        subject_id: UUID,
    ) -> tuple[RefreshSubjectKind, UUID]:
        if kind is not RefreshSubjectKind.TABLE:
            return kind, subject_id
        table: CatalogTableRow | None = self._session.get(CatalogTableRow, subject_id)
        if table is None or table.asset_id is None:
            return kind, subject_id
        return RefreshSubjectKind.ASSET, table.asset_id

    def _asset_kind(self, kind: RefreshSubjectKind, subject_id: UUID) -> str | None:
        if kind is not RefreshSubjectKind.ASSET:
            return None
        asset: AssetRow | None = self._session.get(AssetRow, subject_id)
        if asset is None:
            return None
        return asset.kind

    def _find(self, kind: RefreshSubjectKind, subject_id: UUID) -> EntityConfigurationRow | None:
        if kind is RefreshSubjectKind.SPACE:
            statement = select(EntityConfigurationRow).where(
                EntityConfigurationRow.space_id == subject_id,
            )
        elif kind is RefreshSubjectKind.SCOPE:
            statement = select(EntityConfigurationRow).where(
                EntityConfigurationRow.scope_id == subject_id,
            )
        elif kind is RefreshSubjectKind.ASSET:
            statement = select(EntityConfigurationRow).where(
                EntityConfigurationRow.asset_id == subject_id,
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
            AssetRefreshMode.INTERVAL,
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
        if kind is RefreshSubjectKind.ASSET:
            asset_kind: str | None = self._asset_kind(kind, subject_id)
            if asset_kind is None:
                return dict(_TABLE_OPTIONS)
            return dict(_ASSET_DEFAULTS[AssetKind(asset_kind)])
        return dict(_DEFAULTS[kind])

    def _new_row(
        self,
        kind: RefreshSubjectKind,
        subject_id: UUID,
        interval_seconds: int,
        enabled: bool,
        model: ModelId,
        effort: ReasoningEffort,
        refresh_mode: AssetRefreshMode,
        options: dict[str, bool],
        now: datetime,
    ) -> EntityConfigurationRow:
        return EntityConfigurationRow(
            id=uuid4(),
            space_id=subject_id if kind is RefreshSubjectKind.SPACE else None,
            scope_id=subject_id if kind is RefreshSubjectKind.SCOPE else None,
            catalog_table_id=subject_id if kind is RefreshSubjectKind.TABLE else None,
            asset_id=subject_id if kind is RefreshSubjectKind.ASSET else None,
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
    asset_kind: str | None = None,
) -> dict[str, bool]:
    """Merge stored flags with the defaults for this entity kind."""
    stored: dict[str, bool] = {}
    key: str
    value: object
    for key, value in raw.items():
        if isinstance(value, bool):
            stored[key] = value
    return _checked_options(kind, stored, asset_kind)


def next_rebuild_at(session: Session, row: EntityConfigurationRow) -> datetime | None:
    """Return when the next context rebuild is due. A disabled row has no deadline."""
    if not row.enabled or row.refresh_mode != AssetRefreshMode.INTERVAL.value:
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
    """Job kind and subject this configuration rebuilds. A linked table uses its asset."""
    if row.asset_id is not None:
        return CatalogJobKind.ASSET, row.asset_id
    if row.catalog_table_id is not None:
        table: CatalogTableRow | None = session.get(CatalogTableRow, row.catalog_table_id)
        if table is not None and table.asset_id is not None:
            return CatalogJobKind.ASSET, table.asset_id
        return CatalogJobKind.KNOWLEDGE, row.catalog_table_id
    if row.scope_id is not None:
        return CatalogJobKind.SCOPE, row.scope_id
    if row.space_id is not None:
        return CatalogJobKind.SPACE, row.space_id
    return None, None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _checked_options(
    kind: RefreshSubjectKind,
    options: dict[str, bool],
    asset_kind: str | None = None,
) -> dict[str, bool]:
    if kind is RefreshSubjectKind.ASSET:
        if asset_kind is None:
            allowed: dict[str, bool] = dict(_TABLE_OPTIONS)
        else:
            allowed = dict(_ASSET_DEFAULTS[AssetKind(asset_kind)])
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
    asset_kind: str | None = None,
) -> EntityConfigurationRead:
    option_kind: RefreshSubjectKind = (
        RefreshSubjectKind.ASSET if row.asset_id is not None else kind
    )
    return EntityConfigurationRead(
        subject_kind=kind,
        subject_id=subject_id,
        interval_seconds=row.interval_seconds,
        enabled=row.enabled,
        model=parse_model_id(row.model),
        effort=parse_effort(row.effort),
        refresh_mode=AssetRefreshMode(row.refresh_mode),
        options=option_values(option_kind, row.options, asset_kind),
        next_run_at=next_run_at,
    )
