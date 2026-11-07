"""Beat claims due entity configurations and starts one context rebuild each."""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.models import AssetContextOptions
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import (
    ModelId,
    ReasoningEffort,
    parse_effort,
    parse_model_id,
)
from intelegent_data_catalog.entity_configuration import (
    context_rebuild_running,
    next_rebuild_at,
    option_values,
)
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs
from intelegent_data_catalog.knowledge.models import TableContextOptions
from intelegent_data_catalog.persistence.enums import AssetStatus, RefreshSubjectKind, TableStatus
from intelegent_data_catalog.persistence.models import (
    AssetRow,
    CatalogTableRow,
    EntityConfigurationRow,
)

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.jobs")


@dataclass(frozen=True)
class _QueuedRebuild:
    """One due rebuild copied out of the claim transaction before it commits."""

    kind: RefreshSubjectKind
    subject_id: UUID
    name: str
    options: dict[str, object]
    model: ModelId
    effort: ReasoningEffort


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
        asset_id: UUID | None = row.asset_id
        if asset_id is not None:
            asset: AssetRow | None = self._session.get(AssetRow, asset_id)
            if asset is None or asset.status != AssetStatus.ACTIVE.value:
                return None
            flags: dict[str, bool] = option_values(RefreshSubjectKind.ASSET, options, asset.kind)
            return _QueuedRebuild(
                RefreshSubjectKind.ASSET,
                asset.id,
                asset.name,
                dict(flags),
                parse_model_id(row.model),
                parse_effort(row.effort),
            )
        table_id: UUID | None = row.catalog_table_id
        if table_id is not None:
            table: CatalogTableRow | None = self._session.get(CatalogTableRow, table_id)
            if table is None or table.status != TableStatus.ACTIVE.value:
                return None
            if table.asset_id is not None:
                linked: AssetRow | None = self._session.get(AssetRow, table.asset_id)
                if linked is None or linked.status != AssetStatus.ACTIVE.value:
                    return None
                asset_flags: dict[str, bool] = option_values(
                    RefreshSubjectKind.ASSET,
                    options,
                    linked.kind,
                )
                return _QueuedRebuild(
                    RefreshSubjectKind.ASSET,
                    linked.id,
                    linked.name,
                    dict(asset_flags),
                    parse_model_id(row.model),
                    parse_effort(row.effort),
                )
            return _QueuedRebuild(
                RefreshSubjectKind.TABLE,
                table.id,
                table.name,
                options,
                parse_model_id(row.model),
                parse_effort(row.effort),
            )
        return None

    def _start(self, planned: _QueuedRebuild) -> None:
        if planned.kind is RefreshSubjectKind.ASSET:
            flags: dict[str, bool] = {}
            key: str
            value: object
            for key, value in planned.options.items():
                if isinstance(value, bool):
                    flags[key] = value
            self._jobs.start_asset(
                planned.subject_id,
                planned.name,
                AssetContextOptions(model=planned.model, effort=planned.effort, flags=flags),
            )
            _LOGGER.info("queued asset context rebuild asset_id=%s", planned.subject_id)
            return
        if planned.kind is RefreshSubjectKind.TABLE:
            self._jobs.start_knowledge(
                planned.subject_id,
                planned.name,
                _table_options(planned.options, planned.model, planned.effort),
            )
            _LOGGER.info("queued table context rebuild table_id=%s", planned.subject_id)
            return


def _table_options(
    raw: dict[str, object],
    model: ModelId,
    effort: ReasoningEffort,
) -> TableContextOptions:
    values: dict[str, bool] = option_values(RefreshSubjectKind.TABLE, raw)
    return TableContextOptions(
        include_data_profile=values["include_data_profile"],
        include_graph=values["include_graph"],
        include_lineage=values["include_lineage"],
        model=model,
        effort=effort,
    )

