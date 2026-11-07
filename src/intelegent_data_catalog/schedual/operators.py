"""Claim agent operators whose cron is due and hand them to an executor."""

import logging
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.definitions.models import AgentSpec
from intelegent_data_catalog.agents.dsl.errors import AgentDefinitionError
from intelegent_data_catalog.agents.execution.executor import OperatorExecutor
from intelegent_data_catalog.agents.execution.workload import OperatorWorkload
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    AgentStatus,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
    OperatorRunTrigger,
)
from intelegent_data_catalog.persistence.models import (
    AgentDefinitionFileRow,
    AgentDefinitionItemRow,
    AgentRow,
)

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.agents")
_FINISHED: tuple[str, str] = (
    DefinitionRunStatus.SUCCEEDED.value,
    DefinitionRunStatus.FAILED.value,
)


class AgentOperatorSchedule:
    """Submits one run for each applied agent whose ``next_run_at`` has passed."""

    def __init__(self, session: Session, executor: OperatorExecutor) -> None:
        self._session: Session = session
        self._executor: OperatorExecutor = executor

    def claim_due(self, now: datetime) -> int:
        """Queue due items and submit them after the commit.

        A queued or running item stays untouched so a later tick can try again.
        """
        statement = (
            select(AgentDefinitionItemRow)
            .join(AgentRow, AgentRow.id == AgentDefinitionItemRow.agent_id)
            .where(
                AgentDefinitionItemRow.kind == DefinitionKind.AGENT.value,
                AgentDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
                AgentDefinitionItemRow.schedule.is_not(None),
                AgentDefinitionItemRow.next_run_at.is_not(None),
                AgentDefinitionItemRow.next_run_at <= now,
                AgentDefinitionItemRow.run_status.in_(_FINISHED),
                AgentRow.status == AgentStatus.ACTIVE.value,
            )
        )
        if self._session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(of=AgentDefinitionItemRow, skip_locked=True)
        rows: list[AgentDefinitionItemRow] = list(self._session.scalars(statement))
        queued: list[OperatorWorkload] = []
        item: AgentDefinitionItemRow
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
                _LOGGER.exception("agent operator submit failed item_id=%s", workload_row.item_id)
                continue
            submitted += 1
        return submitted

    def _workload(self, item: AgentDefinitionItemRow, now: datetime) -> OperatorWorkload | None:
        spec: AgentSpec | None = _spec(item)
        if spec is None or len(spec.operators) == 0 or item.agent_id is None:
            item.run_status = DefinitionRunStatus.FAILED.value
            item.run_error = "invalid declaration"
            item.next_run_at = None
            item.updated_at = now
            return None
        agent: AgentRow | None = self._session.get(AgentRow, item.agent_id)
        file_row: AgentDefinitionFileRow | None = self._session.get(
            AgentDefinitionFileRow,
            item.file_id,
        )
        if agent is None or file_row is None:
            item.next_run_at = None
            item.updated_at = utc_now()
            return None
        if file_row.status == DefinitionFileStatus.REMOVED.value:
            item.next_run_at = None
            item.updated_at = utc_now()
            return None
        return OperatorWorkload(
            item_id=item.id,
            agent_id=agent.id,
            agent_name=agent.name,
            file_key=file_row.key,
            item_key=item.item_key,
            operators=list(spec.operators),
            trigger=OperatorRunTrigger.SCHEDULE,
        )


def _spec(item: AgentDefinitionItemRow) -> AgentSpec | None:
    try:
        return AgentSpec.model_validate(item.declaration)
    except (ValidationError, AgentDefinitionError):
        return None
