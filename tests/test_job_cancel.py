"""Cancel stops a live build and releases a row the UI still treats as running."""

import asyncio
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.jobs.cancel import CANCELLED_JOB_ERROR
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs, CatalogJobStore
from intelegent_data_catalog.jobs.lease import LeaseHeartbeat
from intelegent_data_catalog.jobs.models import CatalogJobCancelRead
from intelegent_data_catalog.knowledge.models import TableContextOptions
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import CatalogJobKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import CatalogJobRow


class _RecordingBuilds:
    """Records build enqueues and does not run them."""

    def __init__(self) -> None:
        self.job_ids: list[UUID] = []

    def enqueue_build(self, job_id: UUID) -> None:
        self.job_ids.append(job_id)


class _MemoryCancel:
    """In-process cancel flag."""

    def __init__(self) -> None:
        self.requested: set[UUID] = set()
        self.fail_request: bool = False

    def request(self, job_id: UUID, ttl_seconds: int) -> None:
        del ttl_seconds
        if self.fail_request:
            raise RuntimeError("redis down")
        self.requested.add(job_id)

    def is_requested(self, job_id: UUID) -> bool:
        return job_id in self.requested

    def clear(self, job_id: UUID) -> None:
        self.requested.discard(job_id)


class _MemoryLease:
    """Lease that stays held for the heartbeat test."""

    def __init__(self) -> None:
        self.refreshed: int = 0

    def try_acquire(self, job_id: UUID, ttl_seconds: int) -> str | None:
        del job_id, ttl_seconds
        return "token"

    def refresh(self, job_id: UUID, token: str, ttl_seconds: int) -> bool:
        del job_id, token, ttl_seconds
        self.refreshed += 1
        return True

    def release(self, job_id: UUID, token: str) -> None:
        del job_id, token

    def held(self, job_id: UUID) -> bool:
        del job_id
        return True

    def mark_requeued(self, job_id: UUID, ttl_seconds: int) -> bool:
        del job_id, ttl_seconds
        return True

    def clear_requeued(self, job_id: UUID) -> None:
        del job_id


def test_cancel_fails_a_running_job_and_signals_the_worker(tmp_path: Path) -> None:
    """Cancel marks the row failed and asks the worker to exit."""
    database: CatalogDatabase = _database(tmp_path)
    signal: _MemoryCancel = _MemoryCancel()
    try:
        job_id: UUID = _running_job(database)
        jobs: CatalogJobs = CatalogJobs(database, _RecordingBuilds())
        cancelled: CatalogJobCancelRead | None = jobs.cancel(job_id, signal, 30)
        assert cancelled is not None
        assert cancelled.status == "failed"
        assert cancelled.error == CANCELLED_JOB_ERROR
        assert job_id in signal.requested
        assert _status(database, job_id) == "failed"
        again: CatalogJobCancelRead | None = jobs.cancel(job_id, signal, 30)
        assert again is not None
        assert again.status == "failed"
    finally:
        database.dispose()


def test_cancel_of_a_finished_job_does_not_signal(tmp_path: Path) -> None:
    """A build that already finished stays as it is."""
    database: CatalogDatabase = _database(tmp_path)
    signal: _MemoryCancel = _MemoryCancel()
    try:
        job_id: UUID = _running_job(database)
        CatalogJobStore(database).complete(job_id, "succeeded", None)
        cancelled: CatalogJobCancelRead | None = CatalogJobs(database, _RecordingBuilds()).cancel(
            job_id,
            signal,
            30,
        )
        assert cancelled is not None
        assert cancelled.status == "succeeded"
        assert cancelled.error is None
        assert signal.requested == set()
    finally:
        database.dispose()


def test_cancel_releases_the_row_when_the_signal_cannot_be_written(tmp_path: Path) -> None:
    """A stuck UI row is released even when Redis cannot take the cancel flag."""
    database: CatalogDatabase = _database(tmp_path)
    signal: _MemoryCancel = _MemoryCancel()
    signal.fail_request = True
    try:
        job_id: UUID = _running_job(database)
        cancelled: CatalogJobCancelRead | None = CatalogJobs(database, _RecordingBuilds()).cancel(
            job_id,
            signal,
            30,
        )
        assert cancelled is not None
        assert cancelled.status == "failed"
        assert cancelled.error == CANCELLED_JOB_ERROR
    finally:
        database.dispose()


def test_cancel_route_releases_a_running_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST cancel returns the failed job and does not require a live worker."""
    signal: _MemoryCancel = _MemoryCancel()
    monkeypatch.setattr(
        "intelegent_data_catalog.api.v1.routes.platform.RedisCancelSignal.from_url",
        lambda url: signal,
    )
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        job_id: UUID = _running_job(database)
        missing: httpx.Response = _send(app, "POST", f"/api/v1/catalog-jobs/{uuid4()}/cancel")
        assert missing.status_code == 404
        cancelled: httpx.Response = _send(app, "POST", f"/api/v1/catalog-jobs/{job_id}/cancel")
        assert cancelled.status_code == 200
        body: dict[str, object] = _object(cancelled)
        assert body["status"] == "failed"
        assert body["error"] == CANCELLED_JOB_ERROR
        assert job_id in signal.requested
        assert _status(database, job_id) == "failed"
    finally:
        database.dispose()


def test_heartbeat_stops_the_worker_when_cancel_is_requested() -> None:
    """The lease thread notices a cancel flag without waiting for the model call."""
    job_id: UUID = uuid4()
    signal: _MemoryCancel = _MemoryCancel()
    stopped: list[str] = []
    heartbeat: LeaseHeartbeat = LeaseHeartbeat(
        _MemoryLease(),
        job_id,
        "token",
        90,
        signal,
        on_cancel=lambda: stopped.append("stopped"),
        poll_seconds=0.05,
    )
    heartbeat.start()
    signal.request(job_id, 30)
    deadline: float = time.monotonic() + 2
    while not stopped and time.monotonic() < deadline:
        time.sleep(0.05)
    heartbeat.stop()
    assert stopped == ["stopped"]


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase]:
    database: CatalogDatabase = _database(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    return create_app(container), database


def _running_job(database: CatalogDatabase) -> UUID:
    row: CatalogJobRow
    created: bool
    row, created = CatalogJobStore(database).add_running(
        CatalogJobKind.KNOWLEDGE,
        uuid4(),
        "Sales",
        "",
        TableContextOptions(),
    )
    assert created
    return row.id


def _status(database: CatalogDatabase, job_id: UUID) -> str:
    session_row: CatalogJobRow | None = CatalogJobStore(database).get(job_id)
    assert session_row is not None
    return session_row.status


def _send(app: FastAPI, method: str, path: str) -> httpx.Response:
    return asyncio.run(_request(app, method, path))


async def _request(app: FastAPI, method: str, path: str) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path)


def _object(response: httpx.Response) -> dict[str, object]:
    payload: object = response.json()
    assert isinstance(payload, dict)
    return payload
