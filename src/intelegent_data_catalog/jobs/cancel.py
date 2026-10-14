"""Ask a running catalog job to stop, including one whose worker is already gone."""

import logging
from typing import Protocol, cast
from uuid import UUID

from redis import Redis

from intelegent_data_catalog.core.logger import LOGGER_NAME

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.jobs")

CANCELLED_JOB_ERROR: str = "build cancelled"


class CancelSignal(Protocol):
    """A flag the worker reads while a build is in progress."""

    def request(self, job_id: UUID, ttl_seconds: int) -> None:
        """Mark the job cancelled until the ttl."""

    def is_requested(self, job_id: UUID) -> bool:
        """Return whether cancel was requested and has not expired."""
        ...

    def clear(self, job_id: UUID) -> None:
        """Drop the flag after the worker has observed it."""


class RedisCancelSignal:
    """Cancel flags on the Celery Redis broker."""

    def __init__(self, client: Redis) -> None:
        self._client: Redis = client

    @classmethod
    def from_url(cls, url: str) -> RedisCancelSignal:
        """Open a client for the broker URL. Commands connect on first use."""
        client: Redis = Redis.from_url(url, decode_responses=True)
        return cls(client)

    def request(self, job_id: UUID, ttl_seconds: int) -> None:
        """Mark the job cancelled until the ttl."""
        self._client.set(_cancel_key(job_id), "1", ex=ttl_seconds)

    def is_requested(self, job_id: UUID) -> bool:
        """Return whether cancel was requested and has not expired."""
        found: object = cast(object, self._client.exists(_cancel_key(job_id)))
        return found == 1

    def clear(self, job_id: UUID) -> None:
        """Drop the flag after the worker has observed it."""
        self._client.delete(_cancel_key(job_id))


def _cancel_key(job_id: UUID) -> str:
    return f"catalog:job-cancel:{job_id}"
