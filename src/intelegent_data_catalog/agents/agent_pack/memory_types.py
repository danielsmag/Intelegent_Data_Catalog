"""Notebook types and path rules. Object keys stay compatible with older notes."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydantic import TypeAdapter, ValidationError

from intelegent_data_catalog.core.logger import LOGGER_NAME

_VALID_SEGMENT: re.Pattern[str] = re.compile(r"[A-Za-z0-9_.-]{1,200}")
_MAX_METADATA_CHARS: int = 4_096
_METADATA: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])
logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.memory")


class MemoryPathEscapeError(ValueError):
    """A memory path resolves outside its store directory."""


class MemoryConflictError(RuntimeError):
    """The stored version did not match the mutation's expected version."""


class MemoryOperationConflictError(RuntimeError):
    """An operation id was reused with a different fingerprint."""


@dataclass(frozen=True)
class MemoryFile:
    """One memory file and its compare-and-set version."""

    content: str
    version: str
    operation_id: str | None
    truncated: bool
    metadata: Mapping[str, str] | None = field(default=None, hash=False)


@dataclass(frozen=True)
class MemoryOperation:
    """Stable identity and argument fingerprint for an idempotent mutation."""

    id: str
    fingerprint: str


@dataclass(frozen=True)
class MemoryMutation:
    """Result of a memory write or delete."""

    version: str | None
    replayed: bool
    existed: bool


@dataclass(frozen=True)
class MemorySearchMatch:
    """One bounded lexical-search match."""

    path: str
    snippet: str
    score: float


@dataclass(frozen=True)
class MemorySearchResult:
    """Bounded search results and scan metadata."""

    matches: list[MemorySearchMatch]
    scanned: int
    truncated: bool


def validate_store_path(path: str) -> None:
    """Reject a path that could escape the store root or collapse a segment."""
    segment: str
    for segment in path.split("/"):
        if _VALID_SEGMENT.fullmatch(segment) is None or ".." in segment or segment == ".":
            raise ValueError(f"invalid memory path: {path!r}")


def validate_store_prefix(prefix: str) -> None:
    """Reject a prefix that is not a valid directory under the store root."""
    if prefix:
        validate_store_path(prefix.removesuffix("/"))


def encode_metadata(metadata: Mapping[str, str] | None) -> str | None:
    """Return JSON for write metadata. An empty mapping stores nothing."""
    if not metadata:
        return None
    key: str
    value: str
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("memory metadata keys and values must be strings")
        if "\x00" in key or "\x00" in value:
            raise ValueError("memory metadata keys and values must not contain NUL characters")
    encoded: str = json.dumps(
        dict(metadata),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded) > _MAX_METADATA_CHARS:
        raise ValueError(
            f"memory metadata is {len(encoded)} characters as JSON; "
            f"the limit is {_MAX_METADATA_CHARS}"
        )
    return encoded


def decode_metadata(encoded: object) -> dict[str, str] | None:
    """Parse metadata written by ``encode_metadata``. Anything else reads as none."""
    if encoded is None:
        return None
    if isinstance(encoded, str):
        try:
            parsed: dict[str, str] = _METADATA.validate_json(encoded, strict=True)
        except ValidationError:
            parsed = {}
        else:
            if len(parsed) == 0:
                return None
            return parsed
    logger.warning("Ignoring memory metadata that is not a JSON object of strings")
    return None


def is_direct_child(prefix: str, path: str) -> bool:
    """Whether ``path`` sits directly under ``prefix``."""
    return "/" not in path.removeprefix(prefix)


def _snippet(content: str, query: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    lower: str = content.lower()
    positions: list[int] = [lower.find(term) for term in query.lower().split()]
    found: list[int] = [position for position in positions if position >= 0]
    center: int = min(found) if found else 0
    start: int = max(0, center - max_chars // 3)
    end: int = min(len(content), start + max_chars)
    start = max(0, end - max_chars)
    snippet: str = content[start:end]
    if start:
        snippet = f"...{snippet[3:]}" if len(snippet) >= 3 else "." * len(snippet)
    if end < len(content):
        snippet = f"{snippet[:-3]}..." if len(snippet) >= 3 else "." * len(snippet)
    return snippet


async def lexical_search(
    files: Iterable[tuple[str, str]],
    query: str,
    *,
    limit: int,
    max_files: int,
    max_chars: int,
    score_prefix: str = "",
) -> MemorySearchResult:
    """Score files by term frequency. Path hits count twice."""
    terms: list[str] = [term for term in query.lower().split() if term]
    if not terms or limit <= 0 or max_files <= 0 or max_chars <= 0:
        return MemorySearchResult(matches=[], scanned=0, truncated=False)
    scored: list[tuple[float, str, str]] = []
    scanned: int = 0
    truncated: bool = False
    path: str
    content: str
    for path, content in files:
        if scanned >= max_files:
            truncated = True
            break
        scanned += 1
        lower_path: str = path.removeprefix(score_prefix).lower()
        lower_content: str = content.lower()
        score: float = float(
            sum(lower_content.count(term) + 2 * lower_path.count(term) for term in terms)
        )
        if score:
            scored.append((score, path, content))
    scored.sort(key=lambda item: (-item[0], item[1]))
    matches: list[MemorySearchMatch] = []
    remaining: int = max_chars
    item_score: float
    item_path: str
    item_content: str
    for item_score, item_path, item_content in scored[:limit]:
        visible: str = item_path.removeprefix(score_prefix)
        available: int = remaining - len(visible)
        if available <= 0:
            truncated = True
            break
        snippet: str = _snippet(item_content, query, available)
        matches.append(MemorySearchMatch(path=item_path, snippet=snippet, score=item_score))
        remaining -= len(visible) + len(snippet)
    if len(scored) > len(matches):
        truncated = True
    return MemorySearchResult(matches=matches, scanned=scanned, truncated=truncated)
