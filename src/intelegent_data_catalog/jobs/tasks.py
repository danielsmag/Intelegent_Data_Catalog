"""Celery tasks. Status is written to Postgres, not the Celery result backend."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs
from intelegent_data_catalog.jobs.celery_app import celery_app
from intelegent_data_catalog.jobs.context_refresh import ContextRefreshService
from intelegent_data_catalog.jobs.names import (
    DISPATCH_DUE_SYNCS,
    RUN_CATALOG_JOB,
    SYNC_AIRFLOW_INSTANCE,
    SYNC_TRINO_BINDING,
)
from intelegent_data_catalog.jobs.publisher import CeleryJobPublisher
from intelegent_data_catalog.jobs.runner import (
    execute_airflow_sync,
    execute_catalog_job,
    execute_trino_sync,
)
from intelegent_data_catalog.jobs.runtime import job_container
from intelegent_data_catalog.jobs.schedules import SyncScheduleService
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase


def run_catalog_job(job_id: str) -> None:
    """Run one persisted catalog build."""
    execute_catalog_job(UUID(job_id))


def sync_trino_binding(source_binding_id: str) -> None:
    """Sync one Trino source binding."""
    execute_trino_sync(UUID(source_binding_id))


def sync_airflow_instance(instance_id: str) -> None:
    """Sync one Airflow instance."""
    execute_airflow_sync(UUID(instance_id))


def dispatch_due_syncs() -> int:
    """Claim due sync schedules and due context rebuilds."""
    database: CatalogDatabase = job_container().database()
    session: Session = database.session()
    try:
        now: datetime = utc_now()
        publisher: CeleryJobPublisher = CeleryJobPublisher()
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
    return syncs + refreshes


def _register(name: str, function: object) -> None:
    registered: object = celery_app.task(name=name)(function)
    del registered


_register(RUN_CATALOG_JOB, run_catalog_job)
_register(SYNC_TRINO_BINDING, sync_trino_binding)
_register(SYNC_AIRFLOW_INSTANCE, sync_airflow_instance)
_register(DISPATCH_DUE_SYNCS, dispatch_due_syncs)
