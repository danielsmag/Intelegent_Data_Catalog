"""Run one operator against the catalog and record the outcome on its item."""

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.assets.dsl.operators.base import Operator, OperatorContext
from intelegent_data_catalog.assets.dsl.operators.registry import operator_from_ref
from intelegent_data_catalog.assets.execution.cron import next_cron
from intelegent_data_catalog.assets.execution.runs import AssetRunRecorder, asset_run_log
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.assets.models import AssetContextOptions
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
from intelegent_data_catalog.persistence.models import AssetDefinitionItemRow, AssetRow

_MAX_ERROR: int = 8000


class PlatformOperatorContext(OperatorContext):
    """Catalog-backed context. ``describe`` keeps a user-written context."""

    def __init__(
        self,
        session: Session,
        platform: RequestPlatform,
        settings: AppSettings,
        asset: AssetRow,
        logger: logging.Logger,
    ) -> None:
        super().__init__(asset.id, asset.name, logger)
        self._session: Session = session
        self._platform: RequestPlatform = platform
        self._settings: AppSettings = settings
        self._asset: AssetRow = asset

    def build_context(self, flags: dict[str, bool]) -> None:
        """Build the asset snapshot using the saved model and these flags."""
        model, effort, saved = self._profile()
        merged: dict[str, bool] = option_values(
            RefreshSubjectKind.ASSET,
            {**saved, **flags},
            self._asset.kind,
        )
        self._platform.build_asset_context(
            self.asset_id,
            AssetContextOptions(model=model, effort=effort, flags=merged),
        )

    def describe(self) -> None:
        """Ask the model for business context unless a user already wrote it."""
        if self._asset.context_origin == ContextOrigin.USER.value:
            return
        model, effort, _saved = self._profile()
        self._platform.generate_context(
            self.asset_id,
            ContextGenerationOptions(kind="asset", model=model, effort=effort),
        )

    def _profile(self) -> tuple[ModelId, ReasoningEffort, dict[str, bool]]:
        service: EntityConfigurationService = EntityConfigurationService(
            self._session,
            self._settings.llm.default_model_id(),
            self._settings.llm.effort,
        )
        saved: EntityConfigurationRead = service.get(RefreshSubjectKind.ASSET, self.asset_id)
        return saved.model, saved.effort, dict(saved.options)


class OperatorRunner:
    """Executes one workload on a fresh session and stores the run on the item."""

    def __init__(self, container: AppContainer, logger: logging.Logger) -> None:
        self._container: AppContainer = container
        self._logger: logging.Logger = logger

    def run(self, workload: OperatorWorkload) -> None:
        """Mark the item running, execute, then write the outcome."""
        session: Session = self._container.database().session()
        recorder: AssetRunRecorder = AssetRunRecorder(self._container.database())
        try:
            item: AssetDefinitionItemRow | None = session.get(
                AssetDefinitionItemRow,
                workload.item_id,
            )
            asset: AssetRow | None = session.get(AssetRow, workload.asset_id)
            if item is None or asset is None:
                self._logger.error(
                    "asset operator skipped item_id=%s asset_id=%s",
                    workload.item_id,
                    workload.asset_id,
                )
                return
            item.run_status = DefinitionRunStatus.RUNNING.value
            item.run_error = None
            item.updated_at = utc_now()
            session.commit()
            self._execute(session, workload, asset, recorder)
        finally:
            recorder.detach()
            session.close()

    def _execute(
        self,
        session: Session,
        workload: OperatorWorkload,
        asset: AssetRow,
        recorder: AssetRunRecorder,
    ) -> None:
        """Run the operator and store the task history beside the item status."""
        recorder.open(
            asset_id=workload.asset_id,
            item_id=workload.item_id,
            catalog_job_id=None,
            trigger=workload.trigger,
            operator_type=workload.operator.type,
            operator_params=workload.operator.params,
        )
        recorder.attach(self._logger)
        try:
            operator: Operator = operator_from_ref(workload.operator)
            platform: RequestPlatform = RequestPlatform(
                session,
                self._container.settings(),
                self._container.query_service(),
                self._container.object_store(),
            )
            context: PlatformOperatorContext = PlatformOperatorContext(
                session,
                platform,
                self._container.settings(),
                asset,
                self._logger,
            )
            trace_id: UUID = recorder.run_id if recorder.run_id is not None else workload.asset_id
            subject: BuildTraceSubject = BuildTraceSubject(
                trace_id,
                "asset",
                workload.asset_id,
                workload.asset_name,
            )
            with asset_run_log(recorder), build_trace_scope(subject, MemoryBuildTrace()):
                operator.execute(context, recorder)
        except CatalogError as err:
            session.rollback()
            message: str = str(err)
            recorder.finish(DefinitionRunStatus.FAILED, message)
            self._finish(session, workload.item_id, DefinitionRunStatus.FAILED, message)
            return
        except Exception:
            self._logger.exception("asset operator failed item_id=%s", workload.item_id)
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
        item: AssetDefinitionItemRow | None = session.get(AssetDefinitionItemRow, item_id)
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
