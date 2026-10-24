"""Redis lease that proves a worker still owns a running catalog job."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from typing import Protocol, cast
from uuid import UUID, uuid4

from redis import Redis

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.jobs.cancel import CancelSignal

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.jobs")

_REFRESH_SCRIPT: str = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT: str = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class JobLeaseStore(Protocol):
    """Exclusive ownership of one running catalog job."""

    def try_acquire(self, job_id: UUID, ttl_seconds: int) -> str | None:
        """Take the lease. Return a token, or None when another worker holds it."""

    def refresh(self, job_id: UUID, token: str, ttl_seconds: int) -> bool:
        """Extend the lease when the token still owns it."""

    def release(self, job_id: UUID, token: str) -> None:
        """Drop the lease when the token still owns it."""

    def held(self, job_id: UUID) -> bool:
        """Return whether any worker currently owns the lease."""

    def mark_requeued(self, job_id: UUID, ttl_seconds: int) -> bool:
        """Remember a requeue until the ttl. False when one is already remembered."""

    def clear_requeued(self, job_id: UUID) -> None:
        """Forget a requeue marker after a failed enqueue."""


class RedisJobLeaseStore:
    """Lease keys on the Celery Redis broker."""

    def __init__(self, client: Redis) -> None:
        self._client: Redis = client

    @classmethod
    def from_url(cls, url: str) -> RedisJobLeaseStore:
        """Open a client for the broker URL. Commands connect on first use."""
        client: Redis = Redis.from_url(url, decode_responses=True)
        return cls(client)

    def try_acquire(self, job_id: UUID, ttl_seconds: int) -> str | None:
        """Take the lease. Return a token, or None when another worker holds it."""
        token: str = str(uuid4())
        stored: object = cast(
            object,
            self._client.set(
                _lease_key(job_id),
                token,
                nx=True,
                ex=ttl_seconds,
            ),
        )
        if stored is True:
            return token
        return None

    def refresh(self, job_id: UUID, token: str, ttl_seconds: int) -> bool:
        """Extend the lease when the token still owns it."""
        kept: object = cast(
            object,
            self._client.eval(
                _REFRESH_SCRIPT,
                1,
                _lease_key(job_id),
                token,
                str(ttl_seconds),
            ),
        )
        return kept == 1

    def release(self, job_id: UUID, token: str) -> None:
        """Drop the lease when the token still owns it."""
        self._client.eval(_RELEASE_SCRIPT, 1, _lease_key(job_id), token)

    def held(self, job_id: UUID) -> bool:
        """Return whether any worker currently owns the lease."""
        found: object = cast(object, self._client.exists(_lease_key(job_id)))
        return found == 1

    def mark_requeued(self, job_id: UUID, ttl_seconds: int) -> bool:
        """Remember a requeue until the ttl. False when one is already remembered."""
        stored: object = cast(
            object,
            self._client.set(
                _requeue_key(job_id),
                "1",
                nx=True,
                ex=ttl_seconds,
            ),
        )
        return stored is True

    def clear_requeued(self, job_id: UUID) -> None:
        """Forget a requeue marker after a failed enqueue."""
        self._client.delete(_requeue_key(job_id))


class LeaseHeartbeat:
    """Refreshes one lease until the build returns, including during a model call.

    A cancel flag stops the worker process. A blocked model call does not return,
    and the worker's SIGTERM handler waits for that call, so cancel uses ``os._exit``.
    """

    def __init__(
        self,
        store: JobLeaseStore,
        job_id: UUID,
        token: str,
        ttl_seconds: int,
        cancel: CancelSignal,
        on_cancel: Callable[[], None] | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self._store: JobLeaseStore = store
        self._job_id: UUID = job_id
        self._token: str = token
        self._ttl_seconds: int = ttl_seconds
        self._cancel: CancelSignal = cancel
        self._on_cancel: Callable[[], None] = _exit_process if on_cancel is None else on_cancel
        self._poll_seconds: float = poll_seconds
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Refresh the lease in the background."""
        interval: float = max(self._ttl_seconds / 3, 1.0)
        thread: threading.Thread = threading.Thread(
            target=self._run,
            args=(interval,),
            name=f"catalog-job-lease-{self._job_id}",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Stop refreshing. The caller releases the lease."""
        self._stop.set()
        thread: threading.Thread | None = self._thread
        if thread is not None:
            thread.join(timeout=1)

    def _run(self, interval: float) -> None:
        elapsed: float = 0.0
        while not self._stop.wait(self._poll_seconds):
            if self._cancel.is_requested(self._job_id):
                _LOGGER.info("catalog job cancel requested job_id=%s", self._job_id)
                self._on_cancel()
                return
            elapsed += self._poll_seconds
            if elapsed < interval:
                continue
            elapsed = 0.0
            try:
                kept: bool = self._store.refresh(self._job_id, self._token, self._ttl_seconds)
            except Exception:
                _LOGGER.exception("catalog job lease refresh failed job_id=%s", self._job_id)
                continue
            if not kept:
                _LOGGER.warning("catalog job lease lost job_id=%s", self._job_id)
                return


def _exit_process() -> None:
    """End this worker process so a blocked model call stops."""
    os._exit(1)


def _lease_key(job_id: UUID) -> str:
    return f"catalog:job-lease:{job_id}"


def _requeue_key(job_id: UUID) -> str:
    return f"catalog:job-reclaim:{job_id}"
