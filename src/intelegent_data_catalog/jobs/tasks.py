"""Celery tasks. Status is written to Postgres, not the Celery result backend."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.models import ScanResult
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import AppSettings, CelerySettings
from intelegent_data_catalog.jobs.cancel import RedisCancelSignal
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs, CatalogJobStore
from intelegent_data_catalog.jobs.celery_app import celery_app
from intelegent_data_catalog.jobs.context_refresh import ContextRefreshService
from intelegent_data_catalog.jobs.lease import RedisJobLeaseStore
from intelegent_data_catalog.jobs.names import (
    DISPATCH_DUE_SYNCS,
    RUN_CATALOG_JOB,
    SCAN_ASSET_DEFINITIONS,
    SYNC_AIRFLOW_INSTANCE,
    SYNC_TRINO_BINDING,
)
from intelegent_data_catalog.jobs.publisher import CeleryJobPublisher
from intelegent_data_catalog.jobs.reclaim import AbandonedJobReclaimer
from intelegent_data_catalog.jobs.runner import (
    execute_airflow_sync,
    execute_asset_definition_scan,
    execute_catalog_job,
    execute_trino_sync,
)
from intelegent_data_catalog.jobs.runtime import job_container
from intelegent_data_catalog.jobs.scan_lock import (
    ASSET_DEFINITION_SCAN_LOCK,
    RedisScanLock,
    ScanLock,
)
from intelegent_data_catalog.jobs.schedules import SyncScheduleService
from intelegent_data_catalog.jobs.trace import RedisBuildTrace
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.jobs")


def run_catalog_job(job_id: str) -> None:
    """Run one persisted catalog build."""
    execute_catalog_job(UUID(job_id))


def sync_trino_binding(source_binding_id: str) -> None:
    """Sync one Trino source binding."""
    execute_trino_sync(UUID(source_binding_id))


def sync_airflow_instance(instance_id: str) -> None:
    """Sync one Airflow instance."""
    execute_airflow_sync(UUID(instance_id))


def scan_asset_definitions() -> int:
    """Scan definition files when this worker holds the scan lock."""
    try:
        settings: AppSettings = job_container().settings()
        ttl: int = max(int(settings.asset_definitions.poll_interval_seconds * 5), 1)
        lock: RedisScanLock = RedisScanLock.from_url(settings.celery.broker_url)
        return run_locked_scan(lock, ttl)
    except Exception:
        _LOGGER.exception("asset definition scan failed")
        return 0


def run_locked_scan(lock: ScanLock, ttl_seconds: int) -> int:
    """Run one scan when ``lock`` is free. The lock is released afterwards."""
    if not lock.acquire(ASSET_DEFINITION_SCAN_LOCK, ttl_seconds):
        return 0
    try:
        result: ScanResult = execute_asset_definition_scan()
        return result.parsed + result.removed + result.errors
    finally:
        lock.release(ASSET_DEFINITION_SCAN_LOCK)


def dispatch_due_syncs() -> int:
    """Claim due sync schedules, due context rebuilds, and abandoned builds."""
    database: CatalogDatabase = job_container().database()
    session: Session = database.session()
    now: datetime = utc_now()
    publisher: CeleryJobPublisher = CeleryJobPublisher()
    try:
        syncs: int = SyncScheduleService(session).claim_due(now, publisher)
        refreshes: int = ContextRefreshService(
            session,
            CatalogJobs(database, publisher),
        ).claim_due(now)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    reclaimed: int = _reclaim_abandoned(database, publisher, now)
    return syncs + refreshes + reclaimed


def _reclaim_abandoned(
    database: CatalogDatabase,
    publisher: CeleryJobPublisher,
    now: datetime,
) -> int:
    settings: CelerySettings = job_container().settings().celery
    broker_url: str = settings.broker_url
    reclaimer: AbandonedJobReclaimer = AbandonedJobReclaimer(
        CatalogJobStore(database),
        RedisJobLeaseStore.from_url(broker_url),
        publisher,
        timedelta(seconds=settings.reclaim_after_seconds),
        timedelta(seconds=settings.abandon_after_seconds),
        RedisBuildTrace.from_url(broker_url),
        RedisCancelSignal.from_url(broker_url),
        max(int(settings.visibility_timeout_seconds), 1),
    )
    try:
        return reclaimer.reclaim(now)
    except Exception:
        _LOGGER.exception("abandoned catalog jobs could not be reclaimed")
        return 0


def _register(name: str, function: object, *, acks_late: bool = False) -> None:
    registered: object = celery_app.task(
        name=name,
        acks_late=acks_late,
        reject_on_worker_lost=acks_late,
    )(function)
    del registered


_register(RUN_CATALOG_JOB, run_catalog_job, acks_late=True)
_register(SYNC_TRINO_BINDING, sync_trino_binding)
_register(SYNC_AIRFLOW_INSTANCE, sync_airflow_instance)
_register(SCAN_ASSET_DEFINITIONS, scan_asset_definitions)
_register(DISPATCH_DUE_SYNCS, dispatch_due_syncs)
