"""Beat recovers a running catalog job after its worker disappears."""

import time
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs, CatalogJobStore
from intelegent_data_catalog.jobs.lease import JobLeaseStore
from intelegent_data_catalog.jobs.trace import BuildTraceEvent, MemoryBuildTrace
from intelegent_data_catalog.knowledge.models import TableContextOptions
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import CatalogJobRow
from intelegent_data_catalog.schedual.reclaim import ABANDONED_JOB_ERROR, AbandonedJobReclaimer

_RECLAIM_AFTER: timedelta = timedelta(seconds=120)
_ABANDON_AFTER: timedelta = timedelta(seconds=1800)


class _RecordingBuilds:
    """Records build enqueues and does not run them."""

    def __init__(self) -> None:
        self.job_ids: list[UUID] = []

    def enqueue_build(self, job_id: UUID) -> None:
        self.job_ids.append(job_id)


class _MemoryCancel:
    """Records cancel requests. The worker heartbeat would then exit."""

    def __init__(self) -> None:
        self.requested: list[UUID] = []

    def request(self, job_id: UUID, ttl_seconds: int) -> None:
        """Remember the job that should stop."""
        del ttl_seconds
        self.requested.append(job_id)

    def is_requested(self, job_id: UUID) -> bool:
        """Return whether this test asked the job to stop."""
        return job_id in self.requested

    def clear(self, job_id: UUID) -> None:
        """Drop a recorded cancel."""
        self.requested = [item for item in self.requested if item != job_id]


class _FailingBuilds:
    """Raises on the first enqueue."""

    def __init__(self) -> None:
        self.calls: int = 0

    def enqueue_build(self, job_id: UUID) -> None:
        self.calls += 1
        del job_id
        raise RuntimeError("broker down")


class _MemoryJobLeaseStore:
    """In-process lease with the same rules as Redis."""

    def __init__(self) -> None:
        self._leases: dict[UUID, tuple[str, float]] = {}
        self._requeued: dict[UUID, float] = {}

    def try_acquire(self, job_id: UUID, ttl_seconds: int) -> str | None:
        self._drop_expired_lease(job_id)
        if job_id in self._leases:
            return None
        token: str = str(uuid4())
        self._leases[job_id] = (token, time.monotonic() + ttl_seconds)
        return token

    def refresh(self, job_id: UUID, token: str, ttl_seconds: int) -> bool:
        self._drop_expired_lease(job_id)
        current: tuple[str, float] | None = self._leases.get(job_id)
        if current is None or current[0] != token:
            return False
        self._leases[job_id] = (token, time.monotonic() + ttl_seconds)
        return True

    def release(self, job_id: UUID, token: str) -> None:
        current: tuple[str, float] | None = self._leases.get(job_id)
        if current is not None and current[0] == token:
            del self._leases[job_id]

    def held(self, job_id: UUID) -> bool:
        self._drop_expired_lease(job_id)
        return job_id in self._leases

    def mark_requeued(self, job_id: UUID, ttl_seconds: int) -> bool:
        now: float = time.monotonic()
        expires: float | None = self._requeued.get(job_id)
        if expires is not None and expires > now:
            return False
        self._requeued[job_id] = now + ttl_seconds
        return True

    def clear_requeued(self, job_id: UUID) -> None:
        self._requeued.pop(job_id, None)

    def _drop_expired_lease(self, job_id: UUID) -> None:
        current: tuple[str, float] | None = self._leases.get(job_id)
        if current is not None and current[1] <= time.monotonic():
            del self._leases[job_id]


def test_finished_job_status_is_readable(tmp_path: Path) -> None:
    """A finished row is what the trace panel uses to stop its clock."""
    database: CatalogDatabase = _database(tmp_path)
    publisher: _RecordingBuilds = _RecordingBuilds()
    try:
        job_id: UUID = _running_job(database, publisher)
        assert CatalogJobStore(database).complete(job_id, "failed", "build cancelled") is True
        rows: list[CatalogJobRow] = CatalogJobStore(database).statuses({job_id, uuid4()})
        assert len(rows) == 1
        assert rows[0].id == job_id
        assert rows[0].status == "failed"
        assert rows[0].error == "build cancelled"
        assert rows[0].finished_at is not None
    finally:
        database.dispose()


def test_young_running_job_is_left_alone(tmp_path: Path) -> None:
    """A job still waiting for its first worker is not requeued."""
    database: CatalogDatabase = _database(tmp_path)
    publisher: _RecordingBuilds = _RecordingBuilds()
    try:
        job_id: UUID = _running_job(database, publisher)
        reclaimed: int = _reclaimer(database, publisher, _MemoryJobLeaseStore()).reclaim(utc_now())
        assert reclaimed == 0
        assert publisher.job_ids == [job_id]
        assert _status(database, job_id) == "running"
    finally:
        database.dispose()


def test_dead_job_is_requeued_once_until_the_marker_expires(tmp_path: Path) -> None:
    """A running job with no lease is sent back to the worker a single time."""
    database: CatalogDatabase = _database(tmp_path)
    publisher: _RecordingBuilds = _RecordingBuilds()
    try:
        job_id: UUID = _running_job(database, publisher)
        _age(database, job_id, timedelta(minutes=5))
        reclaimer: AbandonedJobReclaimer = _reclaimer(database, publisher, _MemoryJobLeaseStore())
        assert reclaimer.reclaim(utc_now()) == 1
        assert publisher.job_ids == [job_id, job_id]
        assert reclaimer.reclaim(utc_now()) == 0
        assert publisher.job_ids == [job_id, job_id]
        assert _status(database, job_id) == "running"
    finally:
        database.dispose()


def test_live_lease_blocks_requeue_inside_the_abandon_window(tmp_path: Path) -> None:
    """A live worker is not requeued while the build is still inside the window."""
    database: CatalogDatabase = _database(tmp_path)
    publisher: _RecordingBuilds = _RecordingBuilds()
    leases: _MemoryJobLeaseStore = _MemoryJobLeaseStore()
    try:
        job_id: UUID = _running_job(database, publisher)
        token: str | None = leases.try_acquire(job_id, 300)
        assert token is not None
        _age(database, job_id, timedelta(minutes=5))
        reclaimed: int = _reclaimer(database, publisher, leases).reclaim(utc_now())
        assert reclaimed == 0
        assert publisher.job_ids == [job_id]
        assert _status(database, job_id) == "running"
    finally:
        database.dispose()


def test_live_lease_fails_past_the_abandon_deadline(tmp_path: Path) -> None:
    """A stuck worker is failed once the abandon window ends, and the trace closes."""
    database: CatalogDatabase = _database(tmp_path)
    publisher: _RecordingBuilds = _RecordingBuilds()
    leases: _MemoryJobLeaseStore = _MemoryJobLeaseStore()
    sink: MemoryBuildTrace = MemoryBuildTrace()
    cancel: _MemoryCancel = _MemoryCancel()
    try:
        job_id: UUID = _running_job(database, publisher)
        token: str | None = leases.try_acquire(job_id, 300)
        assert token is not None
        _age(database, job_id, timedelta(hours=2))
        reclaimer: AbandonedJobReclaimer = AbandonedJobReclaimer(
            CatalogJobStore(database),
            leases,
            publisher,
            _RECLAIM_AFTER,
            _ABANDON_AFTER,
            sink,
            cancel,
            30,
        )
        reclaimed: int = reclaimer.reclaim(utc_now())
        assert reclaimed == 1
        assert publisher.job_ids == [job_id]
        assert _status(database, job_id) == "failed"
        assert cancel.requested == [job_id]
        recorded: list[BuildTraceEvent] = sink.read(None)
        assert len(recorded) == 1
        assert recorded[0].step == "failed"
        assert recorded[0].detail == ABANDONED_JOB_ERROR
        assert recorded[0].kind == "table"
        assert recorded[0].subject_name == "orders"
    finally:
        database.dispose()


def test_abandoned_job_fails_and_the_next_build_can_start(tmp_path: Path) -> None:
    """A dead job older than the abandon window is failed so a new build can start."""
    database: CatalogDatabase = _database(tmp_path)
    publisher: _RecordingBuilds = _RecordingBuilds()
    jobs: CatalogJobs = CatalogJobs(database, publisher)
    try:
        subject_id: UUID = uuid4()
        options: TableContextOptions = TableContextOptions()
        first_id: UUID = jobs.start_knowledge(subject_id, "orders", options).id
        _age(database, first_id, timedelta(hours=2))
        reclaimed: int = _reclaimer(database, publisher, _MemoryJobLeaseStore()).reclaim(utc_now())
        assert reclaimed == 1
        assert publisher.job_ids == [first_id]
        stored: CatalogJobRow = _row(database, first_id)
        assert stored.status == "failed"
        assert stored.error == ABANDONED_JOB_ERROR
        second_id: UUID = jobs.start_knowledge(subject_id, "orders", options).id
        assert second_id != first_id
        assert _status(database, second_id) == "running"
    finally:
        database.dispose()


def test_failed_enqueue_clears_the_requeue_marker(tmp_path: Path) -> None:
    """A broker error leaves the job eligible for the next beat tick."""
    database: CatalogDatabase = _database(tmp_path)
    starter: _RecordingBuilds = _RecordingBuilds()
    failing: _FailingBuilds = _FailingBuilds()
    try:
        job_id: UUID = _running_job(database, starter)
        _age(database, job_id, timedelta(minutes=5))
        leases: _MemoryJobLeaseStore = _MemoryJobLeaseStore()
        reclaimer: AbandonedJobReclaimer = _reclaimer(database, failing, leases)
        with pytest.raises(RuntimeError, match="broker down"):
            reclaimer.reclaim(utc_now())
        assert failing.calls == 1
        retry: _RecordingBuilds = _RecordingBuilds()
        recovered: int = _reclaimer(database, retry, leases).reclaim(utc_now())
        assert recovered == 1
        assert retry.job_ids == [job_id]
    finally:
        database.dispose()


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _running_job(database: CatalogDatabase, publisher: _RecordingBuilds) -> UUID:
    jobs: CatalogJobs = CatalogJobs(database, publisher)
    return jobs.start_knowledge(uuid4(), "orders", TableContextOptions()).id


def _reclaimer(
    database: CatalogDatabase,
    publisher: _RecordingBuilds | _FailingBuilds,
    leases: JobLeaseStore,
) -> AbandonedJobReclaimer:
    return AbandonedJobReclaimer(
        CatalogJobStore(database),
        leases,
        publisher,
        _RECLAIM_AFTER,
        _ABANDON_AFTER,
    )


def _age(database: CatalogDatabase, job_id: UUID, age: timedelta) -> None:
    session: Session = database.session()
    try:
        row: CatalogJobRow | None = session.get(CatalogJobRow, job_id)
        assert row is not None
        row.created_at = utc_now() - age
        session.commit()
    finally:
        session.close()


def _status(database: CatalogDatabase, job_id: UUID) -> str:
    return _row(database, job_id).status


def _row(database: CatalogDatabase, job_id: UUID) -> CatalogJobRow:
    session: Session = database.session()
    try:
        row: CatalogJobRow | None = session.get(CatalogJobRow, job_id)
        assert row is not None
        session.expunge(row)
        return row
    finally:
        session.close()
