"""Build trace keeps a short, readable history of context builds."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from intelegent_data_catalog.jobs.trace import (
    BuildTraceEvent,
    BuildTraceSubject,
    BuildTraceWrite,
    MemoryBuildTrace,
    build_trace_scope,
    kind_from_agent,
    publish_build_step,
    step_copy,
    trace_kind_for_job,
    trace_status_for_run,
)
from intelegent_data_catalog.llm.trace import AgentTrace


def test_step_copy_names_tools_and_clips_long_detail() -> None:
    """Tool steps keep the tool name. A long ask is stored clipped."""
    title: str
    detail: str
    title, detail = step_copy("tool_call", 'read_catalog {"tables": 2}')
    assert title == "read_catalog"
    assert detail == '{"tables": 2}'
    long_ask: str = "x" * 2500
    clipped_title: str
    clipped: str
    clipped_title, clipped = step_copy("ask", long_ask)
    assert clipped_title == "Asking the model"
    assert clipped.endswith("...")
    assert len(clipped) == 2003


def test_kind_maps_agents_and_context_jobs() -> None:
    """Filters follow the agent, and a context job follows the subject it writes."""
    assert kind_from_agent("scope-subgraph") == "scope"
    assert kind_from_agent("column-subgraph") == "table"
    assert kind_from_agent("space-context") == "space"
    assert kind_from_agent("airflow-analysis") == "airflow"
    assert trace_kind_for_job("knowledge", {}) == "table"
    assert trace_kind_for_job("context", {"kind": "column"}) == "table"
    assert trace_kind_for_job("context", {"kind": "scope"}) == "scope"
    assert trace_kind_for_job("space", {}) == "space"
    assert trace_kind_for_job("airflow", {}) == "airflow"
    note_title: str
    note_detail: str
    note_title, note_detail = step_copy("note", "Iceberg table metadata\nskipped")
    assert note_title == "Iceberg table metadata"
    assert note_detail == "skipped"
    assert trace_status_for_run("queued") == "running"
    assert trace_status_for_run("succeeded") == "succeeded"
    assert trace_status_for_run("failed") == "failed"
    assert trace_status_for_run("skipped") is None


def test_memory_buffer_reads_after_cursor_and_drops_the_oldest() -> None:
    """The ring keeps 400 steps and a cursor returns only newer ones."""
    sink: MemoryBuildTrace = MemoryBuildTrace()
    index: int
    for index in range(401):
        sink.append(_write(f"step-{index}"))
    stored: list[BuildTraceEvent] = sink.read(None)
    assert len(stored) == 400
    assert stored[0].detail == "step-1"
    assert stored[-1].detail == "step-400"
    cursor: str = stored[0].id
    newer: list[BuildTraceEvent] = sink.read(cursor)
    assert len(newer) == 399
    assert newer[0].id > cursor


def test_publish_requires_a_bound_subject_and_ignores_sink_errors() -> None:
    """An unbound call stores nothing. A broken sink does not raise."""
    idle: MemoryBuildTrace = MemoryBuildTrace()
    publish_build_step("scope-subgraph", "ask", "hello")
    assert idle.read(None) == []
    subject: BuildTraceSubject = _subject()
    sink: MemoryBuildTrace = MemoryBuildTrace()
    with build_trace_scope(subject, sink):
        publish_build_step("scope-subgraph", "ask", "Summarize orders")
    recorded: list[BuildTraceEvent] = sink.read(None)
    assert len(recorded) == 1
    assert recorded[0].title == "Asking the model"
    assert recorded[0].kind == "scope"
    assert recorded[0].subject_name == "orders"
    assert recorded[0].agent == "scope-subgraph"
    with build_trace_scope(subject, _Boom()):
        publish_build_step("scope-subgraph", "ask", "again")


def test_agent_trace_publishes_the_ask_when_a_build_is_bound() -> None:
    """The model ask shows up on the bound job before the run finishes."""
    sink: MemoryBuildTrace = MemoryBuildTrace()
    with build_trace_scope(_subject(), sink):
        AgentTrace("scope-subgraph").start("Summarize orders")
    recorded: list[BuildTraceEvent] = sink.read(None)
    assert len(recorded) == 1
    assert recorded[0].title == "Asking the model"
    assert recorded[0].detail == "Summarize orders"


class _Boom:
    """Sink that fails the way a down broker does."""

    def append(self, event: BuildTraceWrite) -> None:
        """Refuse the write."""
        raise ConnectionError(event.step)

    def read(self, after: str | None) -> list[BuildTraceEvent]:
        """Return nothing."""
        if after is not None:
            return []
        return []


def _subject() -> BuildTraceSubject:
    job_id: UUID = uuid4()
    subject_id: UUID = uuid4()
    return BuildTraceSubject(job_id, "scope", subject_id, "orders")


def _write(detail: str) -> BuildTraceWrite:
    return BuildTraceWrite(
        at=datetime.now(UTC),
        kind="scope",
        subject_name="orders",
        subject_id=uuid4(),
        job_id=uuid4(),
        agent="scope-subgraph",
        step="started",
        title="Build started",
        detail=detail,
    )
