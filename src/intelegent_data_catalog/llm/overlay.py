"""Describe-run text captured for the model call that is already on the stack."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

from pydantic_ai_harness import ArtifactScanner, Memory

from intelegent_data_catalog.assets.agent_pack.memory_store import ObjectMemoryStore
from intelegent_data_catalog.storage.object_store import ObjectStore

_MEMORY_ROOT: str = "agent-memory/"
_MEMORY_CHARS: int = 24_000


class DescribeOverlay:
    """Extra instructions, the active prompt, and the asset notebook."""

    def __init__(
        self,
        instructions: str | None,
        task: str | None,
        asset_id: UUID | None = None,
        store: ObjectStore | None = None,
    ) -> None:
        self.instructions: str | None = instructions
        self.task: str | None = task
        self.asset_id: UUID | None = asset_id
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


def current_memory() -> Memory[object] | None:
    """Notebook for the asset run on the stack. ``None`` for scope and space builds."""
    overlay: DescribeOverlay | None = _CURRENT.get()
    if overlay is None or overlay.asset_id is None or overlay.store is None:
        return None
    return Memory(
        ObjectMemoryStore(overlay.store, _MEMORY_ROOT),
        namespace=str(overlay.asset_id),
        max_memory_size=_MEMORY_CHARS,
        write_metadata={"updated_by": "agent"},
        scanner=ArtifactScanner(),
    )
