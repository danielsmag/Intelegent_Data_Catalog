"""HMAC tickets for the notebook channel socket."""

import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass

from intelegent_data_catalog.exceptions import AuthenticationError


@dataclass(frozen=True, slots=True)
class TicketClaims:
    """Identity bound to one kernel channel."""

    kernel_id: str
    user_id: str
    expires_at: int


class KernelTickets:
    """Signs and checks short-lived channel tickets."""

    def __init__(
        self,
        secret: str,
        ttl_seconds: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._secret: bytes = secret.encode("utf-8")
        self._ttl_seconds: int = ttl_seconds
        self._clock: Callable[[], float] = time.time if clock is None else clock

    def sign(self, kernel_id: str, user_id: str, ttl_seconds: int | None = None) -> str:
        """Return ``kernel.user.exp.signature``."""
        ttl: int = self._ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at: int = int(self._clock()) + ttl
        payload: str = f"{kernel_id}.{user_id}.{expires_at}"
        signature: str = self._signature(payload)
        return f"{payload}.{signature}"

    def verify(self, ticket: str) -> TicketClaims:
        """Return the claims or raise when the ticket is bad or expired."""
        kernel_id, user_id, expires_text, signature = self._parts(ticket)
        payload: str = f"{kernel_id}.{user_id}.{expires_text}"
        expected: str = self._signature(payload)
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("notebook ticket is invalid")
        expires_at: int = int(expires_text)
        if expires_at < int(self._clock()):
            raise AuthenticationError("notebook ticket expired")
        return TicketClaims(kernel_id=kernel_id, user_id=user_id, expires_at=expires_at)

    def _signature(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _parts(self, ticket: str) -> tuple[str, str, str, str]:
        parts: list[str] = ticket.split(".")
        if len(parts) != 4:
            raise AuthenticationError("notebook ticket is invalid")
        kernel_id, user_id, expires_text, signature = parts
        if kernel_id == "" or user_id == "" or not expires_text.isdigit() or signature == "":
            raise AuthenticationError("notebook ticket is invalid")
        return kernel_id, user_id, expires_text, signature
