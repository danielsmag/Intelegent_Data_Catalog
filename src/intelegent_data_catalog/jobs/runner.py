"""Run one persisted catalog job or one sync on the process container."""

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.airflow.service import AirflowService
from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import CelerySettings
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.cancel import CANCELLED_JOB_ERROR, CancelSignal, RedisCancelSignal
from intelegent_data_catalog.jobs.catalog import (
    CatalogAirflowBuild,
    CatalogContextGeneration,
    CatalogKnowledgeBuild,
    CatalogScopeBuild,
    CatalogSpaceBuild,
)
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobStore
from intelegent_data_catalog.jobs.lease import JobLeaseStore, LeaseHeartbeat, RedisJobLeaseStore
from intelegent_data_catalog.jobs.runtime import job_container
from intelegent_data_catalog.jobs.trace import (
    BuildTraceSubject,
    RedisBuildTrace,
    build_trace_scope,
    publish_build_step,
    trace_kind_for_job,
)
from intelegent_data_catalog.knowledge.models import (
    AirflowContextOptions,
    ContextGenerationOptions,
    ScopeContextOptions,
    SpaceContextOptions,
    TableContextOptions,
)
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import CatalogJobKind, CatalogJobStatus
from intelegent_data_catalog.persistence.models import CatalogJobRow

_LOGGER: logging.Logger = logging.getLogger(LOGGER_NAME)

_FAILURE_LOG: dict[str, str] = {
    CatalogJobKind.KNOWLEDGE.value: "knowledge context build failed",
    CatalogJobKind.SCOPE.value: "scope context build failed",
    CatalogJobKind.SPACE.value: "space context build failed",
    CatalogJobKind.CONTEXT.value: "context generation failed",
    CatalogJobKind.AIRFLOW.value: "airflow context build failed",
}
_FAILURE_MESSAGE: dict[str, str] = {
    CatalogJobKind.KNOWLEDGE.value: "context build failed",
    CatalogJobKind.SCOPE.value: "scope context build failed",
    CatalogJobKind.SPACE.value: "space context build failed",
    CatalogJobKind.CONTEXT.value: "context generation failed",
    CatalogJobKind.AIRFLOW.value: "airflow context build failed",
}


def execute_catalog_job(job_id: UUID) -> None:
    """Run a committed catalog job and store its status.

    The Redis lease keeps Beat from starting a second copy. A duplicate delivery
    finds the lease held and returns. The heartbeat covers a long model call.
    """
    container: AppContainer = job_container()
    store: CatalogJobStore = CatalogJobStore(container.database())
    row: CatalogJobRow | None = store.get(job_id)
    if row is None or row.status != CatalogJobStatus.RUNNING.value:
        return
    celery_settings: CelerySettings = container.settings().celery
    broker_url: str = celery_settings.broker_url
    cancels: CancelSignal = RedisCancelSignal.from_url(broker_url)
    if cancels.is_requested(job_id):
        store.complete(job_id, "failed", CANCELLED_JOB_ERROR)
        cancels.clear(job_id)
        return
    leases: JobLeaseStore = RedisJobLeaseStore.from_url(broker_url)
    ttl_seconds: int = max(int(celery_settings.lease_seconds), 1)
    token: str | None = leases.try_acquire(job_id, ttl_seconds)
    if token is None:
        _LOGGER.info("catalog job lease held job_id=%s", job_id)
        return
    current: CatalogJobRow | None = store.get(job_id)
    if current is None or current.status != CatalogJobStatus.RUNNING.value:
        leases.release(job_id, token)
        return
    heartbeat: LeaseHeartbeat = LeaseHeartbeat(leases, job_id, token, ttl_seconds, cancels)
    heartbeat.start()
    subject: BuildTraceSubject = BuildTraceSubject(
        job_id,
        trace_kind_for_job(current.kind, current.options),
        current.subject_id,
        current.subject_name,
    )
    try:
        with build_trace_scope(subject, RedisBuildTrace.from_url(broker_url)):
            publish_build_step("", "started", "")
            _finish(container, store, current)
    finally:
        heartbeat.stop()
        leases.release(job_id, token)


def _finish(container: AppContainer, store: CatalogJobStore, row: CatalogJobRow) -> None:
    job_id: UUID = row.id
    kind: str = row.kind
    subject_id: UUID = row.subject_id
    options: dict[str, object] = row.options
    try:
        _execute(container, kind, subject_id, options)
    except CatalogError as err:
        message: str = str(err)
        store.complete(job_id, "failed", message)
        publish_build_step("", "failed", message)
    except Exception:
        _LOGGER.exception(_FAILURE_LOG.get(kind, "catalog job failed"))
        failed: str = _FAILURE_MESSAGE.get(kind, "catalog job failed")
        store.complete(job_id, "failed", failed)
        publish_build_step("", "failed", failed)
    else:
        store.complete(job_id, "succeeded", None)
        publish_build_step("", "succeeded", "Context saved")


def execute_trino_sync(source_binding_id: UUID) -> None:
    """Run one Trino metadata sync on a fresh session."""
    container: AppContainer = job_container()
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    try:
        platform: RequestPlatform = RequestPlatform(
            session,
            container.settings(),
            container.query_service(),
        )
        platform.sync.run(source_binding_id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def execute_airflow_sync(instance_id: UUID) -> None:
    """Run one Airflow snapshot sync on a fresh session."""
    container: AppContainer = job_container()
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    try:
        service: AirflowService = AirflowService(
            session,
            container.http_client(),
            container.settings().airflow,
            container.logger().getChild("airflow"),
        )
        service.sync(instance_id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _execute(
    container: AppContainer,
    kind: str,
    subject_id: UUID,
    options: dict[str, object],
) -> None:
    match kind:
        case CatalogJobKind.KNOWLEDGE.value:
            CatalogKnowledgeBuild(container).run(
                subject_id,
                TableContextOptions.model_validate(options),
            )
        case CatalogJobKind.SCOPE.value:
            CatalogScopeBuild(container).run(
                subject_id,
                ScopeContextOptions.model_validate(options),
            )
        case CatalogJobKind.SPACE.value:
            CatalogSpaceBuild(container).run(
                subject_id,
                SpaceContextOptions.model_validate(options),
            )
        case CatalogJobKind.CONTEXT.value:
            CatalogContextGeneration(container).run(
                subject_id,
                ContextGenerationOptions.model_validate(options),
            )
        case CatalogJobKind.AIRFLOW.value:
            CatalogAirflowBuild(container).run(
                subject_id,
                AirflowContextOptions.model_validate(options),
            )
        case _:
            raise CatalogError(f"unknown catalog job kind: {kind}")
