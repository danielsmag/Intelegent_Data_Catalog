"""Stop two definition scans from applying the same prefix at once."""

from __future__ import annotations

from typing import Protocol, cast
from uuid import uuid4

from redis import Redis

ASSET_DEFINITION_SCAN_LOCK: str = "agent-definitions:scan"


class ScanLock(Protocol):
    """A lock held for one scan pass."""

    def acquire(self, key: str, ttl_seconds: int) -> bool:
        """Take the lock. False when another scan holds it."""
        ...

    def release(self, key: str) -> None:
        """Release the lock when this caller holds it."""
        ...


class RedisScanLock:
    """Scan lock on the Celery Redis broker."""

    def __init__(self, client: Redis) -> None:
        self._client: Redis = client
        self._tokens: dict[str, str] = {}

    @classmethod
    def from_url(cls, url: str) -> RedisScanLock:
        """Open a client for the broker URL. Commands connect on first use."""
        client: Redis = Redis.from_url(url, decode_responses=True)
        return cls(client)

    def acquire(self, key: str, ttl_seconds: int) -> bool:
        """Take the lock until ``ttl_seconds`` elapses."""
        token: str = uuid4().hex
        stored: object = cast(object, self._client.set(key, token, nx=True, ex=ttl_seconds))
        if stored is not True:
            return False
        self._tokens[key] = token
        return True

    def release(self, key: str) -> None:
        """Drop the lock when the stored token is still ours."""
        token: str | None = self._tokens.pop(key, None)
        if token is None:
            return
        current: object = cast(object, self._client.get(key))
        if current == token:
            self._client.delete(key)
