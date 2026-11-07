"""Claim asset operators whose cron is due and hand them to an executor."""

import logging
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.models import AssetSpec
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.execution.executor import OperatorExecutor
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    AssetStatus,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
    OperatorRunTrigger,
)
from intelegent_data_catalog.persistence.models import (
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
    AssetRow,
)

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.assets")
_FINISHED: tuple[str, str] = (
    DefinitionRunStatus.SUCCEEDED.value,
    DefinitionRunStatus.FAILED.value,
)


class AssetOperatorSchedule:
    """Submits one run for each applied asset whose ``next_run_at`` has passed."""

    def __init__(self, session: Session, executor: OperatorExecutor) -> None:
        self._session: Session = session
        self._executor: OperatorExecutor = executor

    def claim_due(self, now: datetime) -> int:
        """Queue due items and submit them after the commit.

        A queued or running item stays untouched so a later tick can try again.
        """
        statement = (
            select(AssetDefinitionItemRow)
            .join(AssetRow, AssetRow.id == AssetDefinitionItemRow.asset_id)
            .where(
                AssetDefinitionItemRow.kind == DefinitionKind.ASSET.value,
                AssetDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
                AssetDefinitionItemRow.schedule.is_not(None),
                AssetDefinitionItemRow.next_run_at.is_not(None),
                AssetDefinitionItemRow.next_run_at <= now,
                AssetDefinitionItemRow.run_status.in_(_FINISHED),
                AssetRow.status == AssetStatus.ACTIVE.value,
            )
        )
        if self._session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(of=AssetDefinitionItemRow, skip_locked=True)
        rows: list[AssetDefinitionItemRow] = list(self._session.scalars(statement))
        queued: list[OperatorWorkload] = []
        item: AssetDefinitionItemRow
        for item in rows:
            workload: OperatorWorkload | None = self._workload(item, now)
            if workload is None:
                continue
            item.run_status = DefinitionRunStatus.QUEUED.value
            item.run_error = None
            item.updated_at = now
            queued.append(workload)
        self._session.commit()
        submitted: int = 0
        workload_row: OperatorWorkload
        for workload_row in queued:
            try:
                self._executor.submit(workload_row)
            except Exception:
                _LOGGER.exception("asset operator submit failed item_id=%s", workload_row.item_id)
                continue
            submitted += 1
        return submitted

    def _workload(self, item: AssetDefinitionItemRow, now: datetime) -> OperatorWorkload | None:
        spec: AssetSpec | None = _spec(item)
        if spec is None or spec.operator is None or item.asset_id is None:
            item.run_status = DefinitionRunStatus.FAILED.value
            item.run_error = "invalid declaration"
            item.next_run_at = None
            item.updated_at = now
            return None
        asset: AssetRow | None = self._session.get(AssetRow, item.asset_id)
        file_row: AssetDefinitionFileRow | None = self._session.get(
            AssetDefinitionFileRow,
            item.file_id,
        )
        if asset is None or file_row is None:
            item.next_run_at = None
            item.updated_at = utc_now()
            return None
        if file_row.status == DefinitionFileStatus.REMOVED.value:
            item.next_run_at = None
            item.updated_at = utc_now()
            return None
        return OperatorWorkload(
            item_id=item.id,
            asset_id=asset.id,
            asset_name=asset.name,
            file_key=file_row.key,
            item_key=item.item_key,
            operator=spec.operator,
            trigger=OperatorRunTrigger.SCHEDULE,
        )


def _spec(item: AssetDefinitionItemRow) -> AssetSpec | None:
    try:
        return AssetSpec.model_validate(item.declaration)
    except (ValidationError, AssetDefinitionError):
        return None
