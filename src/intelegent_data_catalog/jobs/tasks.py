"""Celery tasks. Status is written to Postgres, not the Celery result backend."""

import logging
from uuid import UUID

from intelegent_data_catalog.assets.definitions.models import ScanResult
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.jobs.celery_app import celery_app
from intelegent_data_catalog.jobs.names import (
    DISPATCH_DUE_SYNCS,
    RUN_ASSET_OPERATOR,
    RUN_CATALOG_JOB,
    SCAN_ASSET_DEFINITIONS,
    SYNC_AIRFLOW_INSTANCE,
    SYNC_TRINO_BINDING,
)
from intelegent_data_catalog.jobs.runner import (
    execute_airflow_sync,
    execute_asset_definition_scan,
    execute_asset_operator,
    execute_catalog_job,
    execute_trino_sync,
)
from intelegent_data_catalog.jobs.runtime import job_container
from intelegent_data_catalog.jobs.scan_lock import (
    ASSET_DEFINITION_SCAN_LOCK,
    RedisScanLock,
    ScanLock,
)
from intelegent_data_catalog.schedual.dispatch import dispatch_due_syncs

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


def run_asset_operator(payload: str) -> None:
    """Run one asset operator queued by a scan or a cron claim."""
    execute_asset_operator(payload)


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
_register(RUN_ASSET_OPERATOR, run_asset_operator, acks_late=True)
_register(DISPATCH_DUE_SYNCS, dispatch_due_syncs)
