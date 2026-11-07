"""One scheduler tick: claim due work, then recover abandoned builds."""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import CelerySettings
from intelegent_data_catalog.jobs.cancel import RedisCancelSignal
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs, CatalogJobStore
from intelegent_data_catalog.jobs.lease import RedisJobLeaseStore
from intelegent_data_catalog.jobs.publisher import CeleryJobPublisher
from intelegent_data_catalog.jobs.runner import build_asset_operator_executor
from intelegent_data_catalog.jobs.runtime import job_container
from intelegent_data_catalog.jobs.trace import RedisBuildTrace
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.schedual.asset_operators import AssetOperatorSchedule
from intelegent_data_catalog.schedual.context_refresh import ContextRefreshService
from intelegent_data_catalog.schedual.reclaim import AbandonedJobReclaimer
from intelegent_data_catalog.schedual.schedules import SyncScheduleService

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.schedual")


def dispatch_due_syncs() -> int:
    """Claim due sync schedules, due context rebuilds, and abandoned builds."""
    database: CatalogDatabase = job_container().database()
    session: Session = database.session()
    now: datetime = utc_now()
    publisher: CeleryJobPublisher = CeleryJobPublisher()
    operators: int = 0
    try:
        syncs: int = SyncScheduleService(session).claim_due(now, publisher)
        refreshes: int = ContextRefreshService(
            session,
            CatalogJobs(database, publisher),
        ).claim_due(now)
        operators = AssetOperatorSchedule(
            session,
            build_asset_operator_executor(job_container().settings()),
        ).claim_due(now)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    reclaimed: int = _reclaim_abandoned(database, publisher, now)
    return syncs + refreshes + operators + reclaimed


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
