"""Run one operator against the catalog and record the outcome on its item."""

import logging
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.agent_pack.compose import explicit_pack_flags
from intelegent_data_catalog.agents.dsl.operators.base import Operator, OperatorContext
from intelegent_data_catalog.agents.dsl.operators.registry import operator_from_ref
from intelegent_data_catalog.agents.dsl.operators.steps import BuildContext
from intelegent_data_catalog.agents.execution.runs import AgentRunRecorder, agent_run_log
from intelegent_data_catalog.agents.execution.tasks import TASK_IDS
from intelegent_data_catalog.agents.execution.workload import OperatorWorkload
from intelegent_data_catalog.agents.models import AgentContextOptions
from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.core.settings import AppSettings, ModelId, ReasoningEffort
from intelegent_data_catalog.entity_configuration import (
    EntityConfigurationRead,
    EntityConfigurationService,
    option_values,
)
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.trace import (
    BuildTraceSubject,
    MemoryBuildTrace,
    build_trace_scope,
)
from intelegent_data_catalog.knowledge.models import ContextGenerationOptions
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    ContextOrigin,
    DefinitionRunStatus,
    RefreshSubjectKind,
)
from intelegent_data_catalog.persistence.models import AgentDefinitionItemRow, AgentRow
from intelegent_data_catalog.schedual.cron import next_cron

_MAX_ERROR: int = 8000


class PlatformOperatorContext(OperatorContext):
    """Catalog-backed context. ``describe`` keeps a user-written context."""

    def __init__(
        self,
        session: Session,
        platform: RequestPlatform,
        settings: AppSettings,
        agent: AgentRow,
        logger: logging.Logger,
    ) -> None:
        super().__init__(agent.id, agent.name, logger)
        self._session: Session = session
        self._platform: RequestPlatform = platform
        self._settings: AppSettings = settings
        self._agent: AgentRow = agent

    def build_context(self, flags: dict[str, bool]) -> None:
        """Build the agent snapshot using the saved model and these flags."""
        model, effort, saved = self._profile()
        merged: dict[str, bool] = option_values(
            RefreshSubjectKind.AGENT,
            {**saved, **flags},
            self._agent.kind,
        )
        self._platform.build_agent_context(
            self.agent_id,
            AgentContextOptions(model=model, effort=effort, flags=merged),
        )

    def has_context(self) -> bool:
        """True when the agent already stores a non-blank context."""
        text: str | None = self._agent.context
        return text is not None and text.strip() != ""

    def describe(self) -> None:
        """Ask the model for business context unless a user already wrote it."""
        if self._agent.context_origin == ContextOrigin.USER.value:
            return
        model, effort, _saved = self._profile()
        self._platform.generate_context(
            self.agent_id,
            ContextGenerationOptions(kind="agent", model=model, effort=effort),
        )

    def _profile(self) -> tuple[ModelId, ReasoningEffort, dict[str, bool]]:
        service: EntityConfigurationService = EntityConfigurationService(
            self._session,
            self._settings.llm.default_model_id(),
            self._settings.llm.effort,
        )
        saved: EntityConfigurationRead = service.get(RefreshSubjectKind.AGENT, self.agent_id)
        return saved.model, saved.effort, dict(saved.options)


class OperatorRunner:
    """Executes one workload on a fresh session and stores the run on the item."""

    def __init__(self, container: AppContainer, logger: logging.Logger) -> None:
        self._container: AppContainer = container
        self._logger: logging.Logger = logger

    def run(self, workload: OperatorWorkload) -> None:
        """Mark the item running, execute, then write the outcome."""
        session: Session = self._container.database().session()
        recorder: AgentRunRecorder = AgentRunRecorder(self._container.database())
        try:
            item: AgentDefinitionItemRow | None = session.get(
                AgentDefinitionItemRow,
                workload.item_id,
            )
            agent: AgentRow | None = session.get(AgentRow, workload.agent_id)
            if item is None or agent is None:
                self._logger.error(
                    "agent operator skipped item_id=%s agent_id=%s",
                    workload.item_id,
                    workload.agent_id,
                )
                return
            item.run_status = DefinitionRunStatus.RUNNING.value
            item.run_error = None
            item.updated_at = utc_now()
            session.commit()
            self._execute(session, workload, agent, recorder)
        finally:
            recorder.detach()
            session.close()

    def _execute(
        self,
        session: Session,
        workload: OperatorWorkload,
        agent: AgentRow,
        recorder: AgentRunRecorder,
    ) -> None:
        """Run every operator in order and store the task history beside the item status."""
        operators: list[Operator] = [operator_from_ref(ref) for ref in workload.operators]
        present: set[str] = {step.task_id for step in operators}
        skip: tuple[str, ...] = tuple(task_id for task_id in TASK_IDS if task_id not in present)
        stored: list[JsonValue] = [
            {"type": ref.type, "params": ref.params} for ref in workload.operators
        ]
        recorder.open(
            agent_id=workload.agent_id,
            item_id=workload.item_id,
            catalog_job_id=None,
            trigger=workload.trigger,
            operator_type=None,
            operator_params={"operators": stored},
            skip=skip,
        )
        recorder.attach(self._logger)
        try:
            platform: RequestPlatform = RequestPlatform(
                session,
                self._container.settings(),
                self._container.query_service(),
                self._container.object_store(),
                self._container.agent_definition_store(),
            )
            context: PlatformOperatorContext = PlatformOperatorContext(
                session,
                platform,
                self._container.settings(),
                agent,
                self._logger,
            )
            trace_id: UUID = recorder.run_id if recorder.run_id is not None else workload.agent_id
            subject: BuildTraceSubject = BuildTraceSubject(
                trace_id,
                "agent",
                workload.agent_id,
                workload.agent_name,
            )
            flags: dict[str, bool] = {}
            step: Operator
            for step in operators:
                if isinstance(step, BuildContext):
                    flags.update(step.context_flags())
            with (
                agent_run_log(recorder),
                build_trace_scope(subject, MemoryBuildTrace()),
                explicit_pack_flags(flags),
            ):
                for step in operators:
                    step.execute(context, recorder)
        except CatalogError as err:
            session.rollback()
            message: str = str(err)
            recorder.finish(DefinitionRunStatus.FAILED, message)
            self._finish(session, workload.item_id, DefinitionRunStatus.FAILED, message)
            return
        except Exception:
            self._logger.exception("agent operator failed item_id=%s", workload.item_id)
            session.rollback()
            recorder.finish(DefinitionRunStatus.FAILED, "operator failed")
            self._finish(
                session,
                workload.item_id,
                DefinitionRunStatus.FAILED,
                "operator failed",
            )
            return
        recorder.finish(DefinitionRunStatus.SUCCEEDED, None)
        self._finish(session, workload.item_id, DefinitionRunStatus.SUCCEEDED, None)

    def _finish(
        self,
        session: Session,
        item_id: UUID,
        status: DefinitionRunStatus,
        error: str | None,
    ) -> None:
        item: AgentDefinitionItemRow | None = session.get(AgentDefinitionItemRow, item_id)
        if item is None:
            return
        now: datetime = utc_now()
        item.run_status = status.value
        item.run_error = _clip(error)
        item.ran_at = now
        schedule: str | None = item.schedule
        if schedule is None:
            item.next_run_at = None
        else:
            item.next_run_at = next_cron(schedule, now)
        item.updated_at = now
        session.commit()


def _clip(message: str | None) -> str | None:
    if message is None or len(message) <= _MAX_ERROR:
        return message
    return message[:_MAX_ERROR]
