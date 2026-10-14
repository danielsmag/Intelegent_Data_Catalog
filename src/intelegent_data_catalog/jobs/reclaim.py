"""Beat recovers catalog jobs whose worker is gone."""

import logging
from datetime import UTC, datetime, timedelta

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobStore
from intelegent_data_catalog.jobs.lease import JobLeaseStore
from intelegent_data_catalog.jobs.publisher import BuildJobPublisher
from intelegent_data_catalog.persistence.models import CatalogJobRow

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.jobs")

ABANDONED_JOB_ERROR: str = "worker exited before the build finished"


class AbandonedJobReclaimer:
    """Requeues a running job with no live lease, and fails one that stays dead."""

    def __init__(
        self,
        jobs: CatalogJobStore,
        leases: JobLeaseStore,
        publisher: BuildJobPublisher,
        reclaim_after: timedelta,
        abandon_after: timedelta,
    ) -> None:
        self._jobs: CatalogJobStore = jobs
        self._leases: JobLeaseStore = leases
        self._publisher: BuildJobPublisher = publisher
        self._reclaim_after: timedelta = reclaim_after
        self._abandon_after: timedelta = abandon_after

    def reclaim(self, now: datetime) -> int:
        """Recover running jobs whose worker no longer holds a lease.

        A job younger than ``reclaim_after`` is still waiting for its first worker.
        A live lease means the worker is inside the build, so the row stays running.
        """
        changed: int = 0
        row: CatalogJobRow
        for row in self._jobs.list_running():
            age: timedelta = now - _aware(row.created_at)
            if age < self._reclaim_after or self._leases.held(row.id):
                continue
            if age >= self._abandon_after:
                self._fail(row, age)
                changed += 1
                continue
            if self._requeue(row):
                changed += 1
        return changed

    def _fail(self, row: CatalogJobRow, age: timedelta) -> None:
        self._jobs.complete(row.id, "failed", ABANDONED_JOB_ERROR)
        _LOGGER.warning(
            "abandoned catalog job job_id=%s kind=%s age_seconds=%s",
            row.id,
            row.kind,
            int(age.total_seconds()),
        )

    def _requeue(self, row: CatalogJobRow) -> bool:
        ttl_seconds: int = max(int(self._reclaim_after.total_seconds()), 1)
        if not self._leases.mark_requeued(row.id, ttl_seconds):
            return False
        try:
            self._publisher.enqueue_build(row.id)
        except Exception:
            self._leases.clear_requeued(row.id)
            raise
        _LOGGER.info("requeued abandoned catalog job job_id=%s kind=%s", row.id, row.kind)
        return True


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
