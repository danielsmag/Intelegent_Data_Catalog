"""Beat claims due entity configurations and starts one context rebuild each."""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.entity_configuration import (
    context_rebuild_running,
    next_rebuild_at,
    option_values,
)
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs
from intelegent_data_catalog.knowledge.models import (
    ScopeContextOptions,
    SpaceContextOptions,
    TableContextOptions,
)
from intelegent_data_catalog.persistence.enums import RefreshSubjectKind
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    EntityConfigurationRow,
    ScopeRow,
    SpaceRow,
)

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.jobs")


@dataclass(frozen=True)
class _QueuedRebuild:
    """One due rebuild copied out of the claim transaction before it commits."""

    kind: RefreshSubjectKind
    subject_id: UUID
    name: str
    options: dict[str, object]


class ContextRefreshService:
    """Starts context rebuilds for due configurations. The caller owns the session."""

    def __init__(self, session: Session, jobs: CatalogJobs) -> None:
        self._session: Session = session
        self._jobs: CatalogJobs = jobs

    def claim_due(self, now: datetime) -> int:
        """Enqueue one rebuild for each enabled configuration whose interval has elapsed.

        A subject with a rebuild already running stays due so a later tick can try again.
        """
        statement = select(EntityConfigurationRow).where(EntityConfigurationRow.enabled.is_(True))
        if self._session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows: list[EntityConfigurationRow] = list(self._session.scalars(statement))
        queued: list[_QueuedRebuild] = []
        row: EntityConfigurationRow
        for row in rows:
            if context_rebuild_running(self._session, row):
                continue
            deadline: datetime | None = next_rebuild_at(self._session, row)
            if deadline is None or deadline > now:
                continue
            planned: _QueuedRebuild | None = self._plan(row)
            if planned is not None:
                queued.append(planned)
        self._session.commit()
        claimed: int = 0
        planned_row: _QueuedRebuild
        for planned_row in queued:
            try:
                self._start(planned_row)
            except CatalogError:
                _LOGGER.exception("context rebuild could not be queued")
                continue
            claimed += 1
        return claimed

    def _plan(self, row: EntityConfigurationRow) -> _QueuedRebuild | None:
        options: dict[str, object] = dict(row.options)
        table_id: UUID | None = row.catalog_table_id
        if table_id is not None:
            table: CatalogTableRow | None = self._session.get(CatalogTableRow, table_id)
            if table is None:
                return None
            return _QueuedRebuild(RefreshSubjectKind.TABLE, table.id, table.name, options)
        scope_id: UUID | None = row.scope_id
        if scope_id is not None:
            scope: ScopeRow | None = self._session.get(ScopeRow, scope_id)
            if scope is None:
                return None
            return _QueuedRebuild(RefreshSubjectKind.SCOPE, scope.id, scope.name, options)
        space_id: UUID | None = row.space_id
        if space_id is None:
            return None
        space: SpaceRow | None = self._session.get(SpaceRow, space_id)
        if space is None:
            return None
        return _QueuedRebuild(RefreshSubjectKind.SPACE, space.id, space.name, options)

    def _start(self, planned: _QueuedRebuild) -> None:
        if planned.kind is RefreshSubjectKind.TABLE:
            self._jobs.start_knowledge(
                planned.subject_id,
                planned.name,
                _table_options(planned.options),
            )
            _LOGGER.info("queued table context rebuild table_id=%s", planned.subject_id)
            return
        if planned.kind is RefreshSubjectKind.SCOPE:
            self._jobs.start_scope(
                planned.subject_id,
                planned.name,
                _scope_options(planned.options),
            )
            _LOGGER.info("queued scope context rebuild scope_id=%s", planned.subject_id)
            return
        self._jobs.start_space(
            planned.subject_id,
            planned.name,
            _space_options(planned.options),
        )
        _LOGGER.info("queued space context rebuild space_id=%s", planned.subject_id)


def _table_options(raw: dict[str, object]) -> TableContextOptions:
    values: dict[str, bool] = option_values(RefreshSubjectKind.TABLE, raw)
    return TableContextOptions(
        include_data_profile=values["include_data_profile"],
        include_graph=values["include_graph"],
        include_lineage=values["include_lineage"],
    )


def _scope_options(raw: dict[str, object]) -> ScopeContextOptions:
    values: dict[str, bool] = option_values(RefreshSubjectKind.SCOPE, raw)
    return ScopeContextOptions(
        include_table_knowledge=values["include_table_knowledge"],
        include_previous=values["include_previous"],
        include_summary=values["include_summary"],
        include_airflow=values["include_airflow"],
    )


def _space_options(raw: dict[str, object]) -> SpaceContextOptions:
    values: dict[str, bool] = option_values(RefreshSubjectKind.SPACE, raw)
    return SpaceContextOptions(
        include_scope_knowledge=values["include_scope_knowledge"],
        include_previous=values["include_previous"],
        include_summary=values["include_summary"],
        include_airflow=values["include_airflow"],
    )
