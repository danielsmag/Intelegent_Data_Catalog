"""Run-scoped ids shared by the worker and each graph call."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

from intelegent_data_catalog.core.settings import LlmSettings

_JOB_ID: ContextVar[str | None] = ContextVar("llm_job_id", default=None)
_RESUME: ContextVar[dict[str, object] | None] = ContextVar("llm_resume", default=None)
_REQUIRE_APPROVAL: ContextVar[bool] = ContextVar("llm_require_approval", default=False)
_DURABLE: ContextVar[bool] = ContextVar("llm_durable", default=False)


def bind_llm_runtime(settings: LlmSettings) -> None:
    """Apply durable and approval flags for the calls that follow on this task."""
    _REQUIRE_APPROVAL.set(settings.require_approval)
    _DURABLE.set(settings.durable)


def approval_required() -> bool:
    """Whether the active settings pause before a guarded tool or proposal."""
    return _REQUIRE_APPROVAL.get()


def durable_runs() -> bool:
    """Whether the active settings persist graph checkpoints."""
    return _DURABLE.get()


def current_job_thread(agent_name: str) -> str | None:
    """Checkpoint thread for the job on the stack. ``None`` outside a job."""
    job_id: str | None = _JOB_ID.get()
    if job_id is None:
        return None
    return f"{job_id}:{agent_name}"


def current_resume() -> dict[str, object] | None:
    """Decision posted for the paused job. ``None`` when the run is not resuming."""
    return _RESUME.get()


@contextmanager
def llm_job(job_id: UUID) -> Iterator[None]:
    """Bind ``job_id`` until the catalog job returns."""
    token: Token[str | None] = _JOB_ID.set(str(job_id))
    try:
        yield
    finally:
        _JOB_ID.reset(token)


@contextmanager
def llm_resume(decision: dict[str, object]) -> Iterator[None]:
    """Bind an approval decision until the resumed job returns."""
    token: Token[dict[str, object] | None] = _RESUME.set(decision)
    try:
        yield
    finally:
        _RESUME.reset(token)
