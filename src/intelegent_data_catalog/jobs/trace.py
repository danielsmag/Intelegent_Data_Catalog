"""Live steps for a context build. Redis holds a short ring; the API reads it."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from redis import Redis

from intelegent_data_catalog.agents.execution.runs import publish_run_event
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import load_settings

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.jobs")

_LIST_KEY: str = "catalog:build-trace"
_SEQ_KEY: str = "catalog:build-trace:seq"
_LIMIT: int = 400

TraceKind = Literal["table", "scope", "space", "airflow", "agent", "asset"]
TraceStep = Literal[
    "started",
    "ask",
    "tool_call",
    "tool_result",
    "retry",
    "text",
    "output",
    "usage",
    "note",
    "succeeded",
    "failed",
]


class BuildTraceWrite(BaseModel):
    """One step before the buffer assigns an id."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    kind: TraceKind
    subject_name: str
    subject_id: UUID
    job_id: UUID
    agent: str
    step: TraceStep
    title: str
    detail: str


class BuildTraceEvent(BuildTraceWrite):
    """One stored step. ``id`` increases for the life of the Redis sequence."""

    id: str


class BuildTraceJob(BaseModel):
    """Catalog row for one trace card. The panel stops its clock from this."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["running", "succeeded", "failed"]
    error: str | None = None
    finished_at: datetime | None = None


class BuildTraceList(BaseModel):
    """A page of steps newer than the caller's cursor."""

    model_config = ConfigDict(extra="forbid")

    events: list[BuildTraceEvent]
    cursor: str | None
    jobs: list[BuildTraceJob] = []


class BuildTraceSubject:
    """The catalog job that owns the steps published on this worker."""

    def __init__(
        self,
        job_id: UUID,
        kind: TraceKind,
        subject_id: UUID,
        subject_name: str,
    ) -> None:
        self.job_id: UUID = job_id
        self.kind: TraceKind = kind
        self.subject_id: UUID = subject_id
        self.subject_name: str = subject_name


class BuildTraceSink(Protocol):
    """Append and read build steps. Tests use an in-memory sink."""

    def append(self, event: BuildTraceWrite) -> None:
        """Store one step and drop the oldest past the cap."""

    def read(self, after: str | None) -> list[BuildTraceEvent]:
        """Return steps whose id is greater than ``after``, oldest first."""


class MemoryBuildTrace:
    """In-memory ring. Used by tests and as the contract Redis follows."""

    def __init__(self) -> None:
        self._events: list[BuildTraceEvent] = []
        self._seq: int = 0

    def append(self, event: BuildTraceWrite) -> None:
        """Store one step and drop the oldest past the cap."""
        self._seq += 1
        stored: BuildTraceEvent = BuildTraceEvent(id=f"{self._seq:016d}", **event.model_dump())
        self._events.append(stored)
        if len(self._events) > _LIMIT:
            self._events = self._events[-_LIMIT:]

    def read(self, after: str | None) -> list[BuildTraceEvent]:
        """Return steps whose id is greater than ``after``, oldest first."""
        return events_after(self._events, after)


class RedisBuildTrace:
    """Build steps on the Celery Redis broker. The list keeps the newest 400."""

    def __init__(self, client: Redis) -> None:
        self._client: Redis = client

    @classmethod
    def from_url(cls, url: str) -> RedisBuildTrace:
        """Open a client for the broker URL. Commands connect on first use."""
        client: Redis = Redis.from_url(url, decode_responses=True)
        return cls(client)

    def append(self, event: BuildTraceWrite) -> None:
        """Store one step and drop the oldest past the cap."""
        seq_raw: object = cast(object, self._client.incr(_SEQ_KEY))
        seq: int = int(str(seq_raw))
        stored: BuildTraceEvent = BuildTraceEvent.model_validate(
            {**event.model_dump(mode="json"), "id": f"{seq:016d}"},
        )
        self._client.lpush(_LIST_KEY, stored.model_dump_json())
        self._client.ltrim(_LIST_KEY, 0, _LIMIT - 1)

    def read(self, after: str | None) -> list[BuildTraceEvent]:
        """Return steps whose id is greater than ``after``. A down broker yields none."""
        try:
            raw: object = cast(object, self._client.lrange(_LIST_KEY, 0, -1))
        except Exception:
            _LOGGER.warning("build trace read failed", exc_info=True)
            return []
        return events_after(_parse_events(raw), after)


_subject: ContextVar[BuildTraceSubject | None] = ContextVar("build_trace_subject", default=None)
_sink: ContextVar[BuildTraceSink | None] = ContextVar("build_trace_sink", default=None)


@contextmanager
def build_trace_scope(subject: BuildTraceSubject, sink: BuildTraceSink) -> Iterator[None]:
    """Bind the job that later model steps should attach to."""
    subject_token: Token[BuildTraceSubject | None] = _subject.set(subject)
    sink_token: Token[BuildTraceSink | None] = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(sink_token)
        _subject.reset(subject_token)


def close_build_trace(
    sink: BuildTraceSink,
    *,
    job_id: UUID,
    kind: str,
    subject_id: UUID,
    subject_name: str,
    options: dict[str, object],
    step: TraceStep,
    detail: str,
) -> None:
    """Store a terminal step for a job that ended outside the worker scope."""
    subject: BuildTraceSubject = BuildTraceSubject(
        job_id,
        trace_kind_for_job(kind, options),
        subject_id,
        subject_name,
    )
    with build_trace_scope(subject, sink):
        publish_build_step("", step, detail)


def publish_build_step(agent: str, step: TraceStep, body: str) -> None:
    """Store one step when a build subject is bound. A sink error does not fail the build."""
    title: str
    detail: str
    title, detail = step_copy(step, body)
    subject: BuildTraceSubject | None = _subject.get()
    sink: BuildTraceSink | None = _sink.get()
    if subject is not None and sink is not None:
        event: BuildTraceWrite = BuildTraceWrite(
            at=datetime.now(UTC),
            kind=subject.kind,
            subject_name=subject.subject_name,
            subject_id=subject.subject_id,
            job_id=subject.job_id,
            agent=agent,
            step=step,
            title=title,
            detail=detail,
        )
        try:
            sink.append(event)
        except Exception:
            _LOGGER.warning("build trace publish failed", exc_info=True)
    publish_run_event(step, title, detail)


def kind_from_agent(agent: str) -> TraceKind:
    """Map a model agent name onto the filter the panel shows."""
    if agent.startswith("airflow"):
        return "airflow"
    if agent.startswith("space"):
        return "space"
    if agent.startswith("scope"):
        return "scope"
    return "table"


def trace_status_for_run(status: str) -> Literal["running", "succeeded", "failed"] | None:
    """Map an operator-run status onto the clock the stream panel reads."""
    if status == "queued" or status == "running":
        return "running"
    if status == "succeeded":
        return "succeeded"
    if status == "failed":
        return "failed"
    return None


def trace_kind_for_job(kind: str, options: dict[str, object]) -> TraceKind:
    """Map a catalog job onto a panel filter. Context jobs use the subject they write."""
    if kind == "scope":
        return "scope"
    if kind == "space":
        return "space"
    if kind == "airflow":
        return "airflow"
    if kind == "agent":
        return "agent"
    if kind == "context":
        raw: object = options.get("kind")
        if raw == "space":
            return "space"
        if raw == "scope":
            return "scope"
        if raw == "airflow":
            return "airflow"
        if raw == "agent":
            return "agent"
        return "table"
    return "table"


def step_copy(step: str, body: str) -> tuple[str, str]:
    """Title and clipped detail for one step."""
    detail: str = body.strip()
    detail_chars: int = load_settings().context.trace_detail_chars
    if len(detail) > detail_chars:
        detail = f"{detail[:detail_chars]}..."
    if step == "started":
        return "Build started", detail
    if step == "ask":
        return "Asking the model", detail
    if step == "tool_call":
        name: str
        rest: str
        name, rest = _split_tool(detail)
        return name or "Tool", rest
    if step == "tool_result":
        result_name: str
        result_rest: str
        result_name, result_rest = _split_tool(detail)
        return result_name or "Result", result_rest
    if step == "retry":
        return "Retry", detail
    if step == "output" or step == "text":
        return "Model answer", detail
    if step == "usage":
        return "Tokens", detail
    if step == "note":
        title, _, rest = detail.partition("\n")
        return title or "Step", rest
    if step == "succeeded":
        return "Saved", detail or "Context saved"
    if step == "failed":
        return "Failed", detail or "Build failed."
    return step, detail


def events_after(events: list[BuildTraceEvent], after: str | None) -> list[BuildTraceEvent]:
    """Keep steps newer than the cursor, oldest first."""
    ordered: list[BuildTraceEvent] = sorted(events, key=_event_id)
    if after is None or after == "":
        return ordered
    kept: list[BuildTraceEvent] = []
    event: BuildTraceEvent
    for event in ordered:
        if event.id > after:
            kept.append(event)
    return kept


def _event_id(event: BuildTraceEvent) -> str:
    return event.id


def _split_tool(detail: str) -> tuple[str, str]:
    name: str
    rest: str
    name, _, rest = detail.partition(" ")
    return name, rest


def _parse_events(raw: object) -> list[BuildTraceEvent]:
    if not isinstance(raw, list):
        return []
    parsed: list[BuildTraceEvent] = []
    item: object
    for item in raw:
        if not isinstance(item, str):
            continue
        try:
            parsed.append(BuildTraceEvent.model_validate_json(item))
        except (ValidationError, json.JSONDecodeError):
            continue
    return parsed
