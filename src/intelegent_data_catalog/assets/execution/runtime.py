"""Run one asset operator workload and record the outcome on its item."""

import logging
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.assets.dsl.operators.registry import operator_from_ref
from intelegent_data_catalog.assets.execution.context import CatalogOperatorContext
from intelegent_data_catalog.assets.execution.outputs import AssetOutputStore
from intelegent_data_catalog.assets.execution.runs import AssetRunRecorder, asset_run_log
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.graph.neo4j_store import Neo4jProfileGraph, Neo4jProjection
from intelegent_data_catalog.jobs.trace import (
    BuildTraceSubject,
    RedisBuildTrace,
    build_trace_scope,
    publish_build_step,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import DefinitionRunStatus
from intelegent_data_catalog.persistence.models import AssetDefinitionItemRow, AssetRow
from intelegent_data_catalog.schedual.cron import ScheduleError, next_cron

_MAX_ERROR: int = 8000


class AssetOperatorRunner:
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
        operators: list[Operator] = [operator_from_ref(ref) for ref in workload.operators]
        task_ids: tuple[str, ...] = tuple(step.task_id for step in operators)
        stored: list[JsonValue] = [
            {"type": ref.type, "params": ref.params} for ref in workload.operators
        ]
        recorder.open(
            asset_id=workload.asset_id,
            item_id=workload.item_id,
            trigger=workload.trigger,
            force=workload.force,
            operator_params={"operators": stored},
            task_ids=task_ids,
        )
        if recorder.run_id is None:
            self._finish(
                session,
                workload.item_id,
                DefinitionRunStatus.FAILED,
                "run was not stored",
            )
            return
        run_id: UUID = recorder.run_id
        recorder.attach(self._logger)
        outputs: AssetOutputStore = AssetOutputStore(
            self._container.asset_definition_store(),
            session,
            self._container.settings().asset_definitions,
            asset.id,
            asset.key,
            recorder.run_id,
        )
        context: CatalogOperatorContext = CatalogOperatorContext(
            session,
            asset,
            self._logger,
            force=workload.force,
            outputs=outputs,
            iceberg=self._container.polaris_client(),
            query=self._container.query_service(),
            github=self._container.github_client(),
            http_client=self._container.http_client(),
            airflow_settings=self._container.settings().airflow,
            profile_graph=_profile_graph(self._container.neo4j_projection()),
        )
        subject: BuildTraceSubject = BuildTraceSubject(run_id, "asset", asset.id, asset.name)
        broker_url: str = self._container.settings().celery.broker_url
        with build_trace_scope(subject, RedisBuildTrace.from_url(broker_url)):
            publish_build_step("", "started", "")
            try:
                with asset_run_log(recorder):
                    step: Operator
                    for step in operators:
                        context.set_task(step.task_id)
                        step.execute(context, recorder)
            except CatalogError as err:
                session.rollback()
                message: str = str(err)
                recorder.finish(DefinitionRunStatus.FAILED, message)
                self._finish(session, workload.item_id, DefinitionRunStatus.FAILED, message)
                publish_build_step("", "failed", message)
                return
            except Exception:
                self._logger.exception("asset operator failed item_id=%s", workload.item_id)
                session.rollback()
                failed: str = "operator failed"
                recorder.finish(DefinitionRunStatus.FAILED, failed)
                self._finish(session, workload.item_id, DefinitionRunStatus.FAILED, failed)
                publish_build_step("", "failed", failed)
                return
            recorder.finish(DefinitionRunStatus.SUCCEEDED, None)
            self._finish(session, workload.item_id, DefinitionRunStatus.SUCCEEDED, None)
            publish_build_step("", "succeeded", "Run finished")

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
            try:
                item.next_run_at = next_cron(schedule, now)
            except ScheduleError:
                item.next_run_at = None
        item.updated_at = now
        session.commit()


def _profile_graph(projection: Neo4jProjection | None) -> Neo4jProfileGraph | None:
    if projection is None:
        return None
    return Neo4jProfileGraph(projection)


def _clip(message: str | None) -> str | None:
    if message is None or len(message) <= _MAX_ERROR:
        return message
    return message[:_MAX_ERROR]
