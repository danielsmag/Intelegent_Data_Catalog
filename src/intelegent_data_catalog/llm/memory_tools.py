"""Notebook tools bound while an agent pack describe is on the stack."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool, tool

from intelegent_data_catalog.agents.agent_pack.memory_types import (
    MemoryFile,
    MemorySearchMatch,
    MemorySearchResult,
)

if TYPE_CHECKING:
    from intelegent_data_catalog.agents.agent_pack.memory_store import ObjectMemoryStore
from intelegent_data_catalog.llm.errors import ToolRetry
from intelegent_data_catalog.llm.scanner import ArtifactScanner


class MemoryTools:
    """One agent's notebook: preload, tools, and the secret scan."""

    def __init__(
        self,
        store: ObjectMemoryStore,
        *,
        namespace: str,
        max_memory_size: int,
        write_metadata: Mapping[str, str],
        scanner: ArtifactScanner,
    ) -> None:
        self.store: ObjectMemoryStore = store
        self.namespace: str = namespace
        self.max_memory_size: int = max_memory_size
        self.write_metadata: Mapping[str, str] = write_metadata
        self.scanner: ArtifactScanner = scanner

    async def preamble(self) -> str:
        """Return the saved ``MEMORY.md`` so the model sees it without a tool call."""
        found: MemoryFile | None = await self.store.read(
            f"{self.namespace}/main/MEMORY.md",
            max_chars=self.max_memory_size,
        )
        if found is None or found.content.strip() == "":
            return ""
        return f"Notebook:\n{found.content}"

    def tools(self) -> list[BaseTool]:
        """Tools that read and write this notebook."""
        return [memory_read, memory_write, memory_list, memory_search]


def _active() -> MemoryTools:
    from intelegent_data_catalog.llm.overlay import current_memory

    found: MemoryTools | None = current_memory()
    if found is None:
        raise ToolRetry("no notebook is active")
    return found


def _path(relative: str) -> str:
    memory: MemoryTools = _active()
    cleaned: str = relative.strip().lstrip("/")
    if cleaned == "":
        raise ToolRetry("memory path is empty")
    if cleaned.startswith(f"{memory.namespace}/"):
        return cleaned
    return f"{memory.namespace}/{cleaned}"


@tool
async def memory_read(path: str) -> str:
    """Read one markdown note. path is relative to this agent, such as main/MEMORY.md."""
    memory: MemoryTools = _active()
    found: MemoryFile | None = await memory.store.read(
        _path(path),
        max_chars=memory.max_memory_size,
    )
    if found is None:
        return ""
    return found.content


@tool
async def memory_write(path: str, content: str) -> str:
    """Replace one markdown note. path is relative to this agent."""
    memory: MemoryTools = _active()
    refusal: str | None = memory.scanner.scan(content)
    if refusal is not None:
        raise ToolRetry(refusal)
    await memory.store.write_with_metadata(
        _path(path),
        content,
        expected_version=None,
        metadata=dict(memory.write_metadata),
    )
    return "saved"


@tool
async def memory_list(prefix: str = "") -> str:
    """List markdown notes under prefix. Leave prefix empty to list the agent notebook."""
    memory: MemoryTools = _active()
    root: str = memory.namespace if prefix.strip() == "" else _path(prefix)
    if not root.endswith("/"):
        root = f"{root}/"
    paths: list[str] = await memory.store.list_paths(root, limit=50)
    visible: list[str] = [item.removeprefix(f"{memory.namespace}/") for item in paths]
    return "\n".join(visible)


@tool
async def memory_search(query: str, prefix: str = "") -> str:
    """Search markdown notes. prefix limits the search to one folder."""
    memory: MemoryTools = _active()
    root: str = f"{memory.namespace}/" if prefix.strip() == "" else _path(prefix)
    if not root.endswith("/"):
        root = f"{root}/"
    found: MemorySearchResult = await memory.store.search(
        root,
        query,
        limit=10,
        max_files=20,
        max_chars=memory.max_memory_size,
        max_file_chars=memory.max_memory_size,
    )
    lines: list[str] = []
    match: MemorySearchMatch
    for match in found.matches:
        relative: str = match.path.removeprefix(f"{memory.namespace}/")
        lines.append(f"{relative}: {match.snippet}")
    return "\n".join(lines)
