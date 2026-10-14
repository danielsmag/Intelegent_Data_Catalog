"""Beat recovers catalog jobs whose worker is gone."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.jobs.cancel import CancelSignal
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobStore
from intelegent_data_catalog.jobs.lease import JobLeaseStore
from intelegent_data_catalog.jobs.publisher import BuildJobPublisher
from intelegent_data_catalog.jobs.trace import BuildTraceSink, TraceStep, close_build_trace
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
        trace: BuildTraceSink | None = None,
        cancel: CancelSignal | None = None,
        cancel_ttl_seconds: int = 60,
    ) -> None:
        self._jobs: CatalogJobStore = jobs
        self._leases: JobLeaseStore = leases
        self._publisher: BuildJobPublisher = publisher
        self._reclaim_after: timedelta = reclaim_after
        self._abandon_after: timedelta = abandon_after
        self._trace: BuildTraceSink | None = trace
        self._cancel: CancelSignal | None = cancel
        self._cancel_ttl_seconds: int = cancel_ttl_seconds

    def reclaim(self, now: datetime) -> int:
        """Recover running jobs whose worker no longer holds a lease.

        A job younger than ``reclaim_after`` is still waiting for its first worker.
        A live lease blocks a requeue, so a second worker does not start. Once the
        job is older than ``abandon_after``, it fails even if that lease is still
        held. A stuck model call would otherwise keep the trace clock running.
        """
        changed: int = 0
        row: CatalogJobRow
        for row in self._jobs.list_running():
            age: timedelta = now - _aware(row.created_at)
            if age < self._reclaim_after:
                continue
            if age >= self._abandon_after:
                if self._fail(row, age):
                    changed += 1
                continue
            if self._leases.held(row.id):
                continue
            if self._requeue(row):
                changed += 1
        return changed

    def _fail(self, row: CatalogJobRow, age: timedelta) -> bool:
        wrote: bool = self._jobs.complete(row.id, "failed", ABANDONED_JOB_ERROR)
        if not wrote:
            return False
        _LOGGER.warning(
            "abandoned catalog job job_id=%s kind=%s age_seconds=%s",
            row.id,
            row.kind,
            int(age.total_seconds()),
        )
        self._stop_worker(row.id)
        self._publish_failure(row)
        return True

    def _stop_worker(self, job_id: UUID) -> None:
        signal: CancelSignal | None = self._cancel
        if signal is None:
            return
        try:
            signal.request(job_id, self._cancel_ttl_seconds)
        except Exception:
            _LOGGER.exception("abandoned catalog job cancel failed job_id=%s", job_id)

    def _publish_failure(self, row: CatalogJobRow) -> None:
        sink: BuildTraceSink | None = self._trace
        if sink is None:
            return
        step: TraceStep = "failed"
        try:
            close_build_trace(
                sink,
                job_id=row.id,
                kind=row.kind,
                subject_id=row.subject_id,
                subject_name=row.subject_name,
                options=row.options,
                step=step,
                detail=ABANDONED_JOB_ERROR,
            )
        except Exception:
            _LOGGER.exception("abandoned catalog job trace failed job_id=%s", row.id)

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
