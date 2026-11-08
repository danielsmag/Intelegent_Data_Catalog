"""Describe-run text captured for the model call that is already on the stack."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

from intelegent_data_catalog.llm.memory_tools import MemoryTools
from intelegent_data_catalog.llm.scanner import ArtifactScanner
from intelegent_data_catalog.storage.object_store import ObjectStore

_MEMORY_ROOT: str = "agent-memory/"
_MEMORY_CHARS: int = 24_000


class DescribeOverlay:
    """Extra instructions, the active prompt, and the agent notebook."""

    def __init__(
        self,
        instructions: str | None,
        task: str | None,
        agent_id: UUID | None = None,
        store: ObjectStore | None = None,
    ) -> None:
        self.instructions: str | None = instructions
        self.task: str | None = task
        self.agent_id: UUID | None = agent_id
        self.store: ObjectStore | None = store


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


def current_memory() -> MemoryTools | None:
    """Notebook for the agent run on the stack. ``None`` for schema builds."""
    overlay: DescribeOverlay | None = _CURRENT.get()
    if overlay is None or overlay.agent_id is None or overlay.store is None:
        return None
    from intelegent_data_catalog.agents.agent_pack.memory_store import ObjectMemoryStore

    return MemoryTools(
        ObjectMemoryStore(overlay.store, _MEMORY_ROOT),
        namespace=str(overlay.agent_id),
        max_memory_size=_MEMORY_CHARS,
        write_metadata={"updated_by": "agent"},
        scanner=ArtifactScanner(),
    )
