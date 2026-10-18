"""Channel tickets expire and reject tampering."""

import pytest

from intelegent_data_catalog.exceptions import AuthenticationError
from intelegent_data_catalog.notebooks.tickets import KernelTickets, TicketClaims


def test_sign_and_verify() -> None:
    """A fresh ticket round-trips to the same kernel and user."""
    clock: list[float] = [1_000.0]
    tickets: KernelTickets = KernelTickets("secret", 30, clock=lambda: clock[0])
    ticket: str = tickets.sign("kernel-1", "user-1")
    claims: TicketClaims = tickets.verify(ticket)
    assert claims.kernel_id == "kernel-1"
    assert claims.user_id == "user-1"
    assert claims.expires_at == 1_030


def test_expired_ticket_is_rejected() -> None:
    """Verification fails once the clock passes the expiry."""
    clock: list[float] = [1_000.0]
    tickets: KernelTickets = KernelTickets("secret", 10, clock=lambda: clock[0])
    ticket: str = tickets.sign("kernel-1", "user-1")
    clock[0] = 1_011.0
    with pytest.raises(AuthenticationError, match="expired"):
        tickets.verify(ticket)


def test_tampered_ticket_is_rejected() -> None:
    """Changing the signature or the payload fails closed."""
    tickets: KernelTickets = KernelTickets("secret", 30)
    ticket: str = tickets.sign("kernel-1", "user-1")
    flipped: str = ticket[:-1] + ("a" if not ticket.endswith("a") else "b")
    with pytest.raises(AuthenticationError, match="invalid"):
        tickets.verify(flipped)
    with pytest.raises(AuthenticationError, match="invalid"):
        tickets.verify("not-a-ticket")
