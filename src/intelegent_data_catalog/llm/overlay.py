"""Describe-run text captured for the model call that is already on the stack."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token


class DescribeOverlay:
    """Extra instructions and the active prompt for one describe or build."""

    def __init__(self, instructions: str | None, task: str | None) -> None:
        self.instructions: str | None = instructions
        self.task: str | None = task


_CURRENT: ContextVar[DescribeOverlay | None] = ContextVar("agent_pack_overlay", default=None)


@contextmanager
def describe_overlay(overlay: DescribeOverlay | None) -> Iterator[None]:
    """Apply ``overlay`` until the wrapped describe returns."""
    token: Token[DescribeOverlay | None] = _CURRENT.set(overlay)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_instructions() -> str | None:
    """Instructions the active describe added. ``None`` outside a pack."""
    overlay: DescribeOverlay | None = _CURRENT.get()
    if overlay is None:
        return None
    return overlay.instructions


def current_task() -> str | None:
    """Active prompt body. ``None`` when no prompt is active."""
    overlay: DescribeOverlay | None = _CURRENT.get()
    if overlay is None:
        return None
    return overlay.task
