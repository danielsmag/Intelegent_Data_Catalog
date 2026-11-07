"""Persist and read agent operator runs on a session the operator cannot roll back."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.execution.models import (
    AgentRunEventRead,
    AgentRunRead,
    AgentRunTaskDetail,
    AgentRunTaskSummary,
)
from intelegent_data_catalog.agents.execution.tasks import TASK_IDS
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    DefinitionRunStatus,
    OperatorRunTrigger,
    OperatorTaskState,
)
from intelegent_data_catalog.persistence.models import AgentOperatorRunRow, AgentOperatorRunTaskRow

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.agents")
_MAX_ERROR: int = 8000
_LOG_LIMIT: int = 100_000
_LINE_LIMIT: int = 8_000
_EVENT_LIMIT: int = 200
_DETAIL_LIMIT: int = 8_000

_current_run: ContextVar[AgentRunRecorder | None] = ContextVar("agent_run_log", default=None)


class AgentRunRecorder:
    """Writes one run on its own sessions. A log failure does not fail the build."""

    def __init__(self, database: CatalogDatabase) -> None:
        self._database: CatalogDatabase = database
        self._run_id: UUID | None = None
        self._task_id: str | None = None
        self._lines: list[str] = []
        self._events: list[dict[str, object]] = []
        self._handler: logging.Handler | None = None
        self._loggers: list[logging.Logger] = []

    @property
    def run_id(self) -> UUID | None:
        """Return the open run, or none when the row could not be stored."""
        return self._run_id

    def open(
        self,
        *,
        agent_id: UUID,
        catalog_job_id: UUID | None,
        trigger: OperatorRunTrigger,
        operator_type: str | None,
        operator_params: Mapping[str, JsonValue],
        skip: tuple[str, ...] = (),
    ) -> None:
        """Insert the run and both tasks. Skipped tasks are stored immediately."""
        if self._run_id is not None:
            return
        session: Session = self._database.session()
        try:
            prior: int = _count_runs(session, agent_id)
            now: datetime = utc_now()
            run_id: UUID = uuid4()
            params: dict[str, object] = {}
            param_key: str
            param_value: JsonValue
            for param_key, param_value in operator_params.items():
                params[param_key] = param_value
            session.add(
                AgentOperatorRunRow(
                    id=run_id,
                    agent_id=agent_id,
                    catalog_job_id=catalog_job_id,
                    status=DefinitionRunStatus.RUNNING.value,
                    trigger=trigger.value,
                    try_number=prior + 1,
                    operator_type=operator_type,
                    operator_params=params,
                    error=None,
                    queued_at=now,
                    started_at=now,
                    finished_at=None,
                ),
            )
            task_id: str
            for task_id in TASK_IDS:
                skipped: bool = task_id in skip
                session.add(
                    AgentOperatorRunTaskRow(
                        id=uuid4(),
                        run_id=run_id,
                        task_id=task_id,
                        state=(
                            OperatorTaskState.SKIPPED.value
                            if skipped
                            else OperatorTaskState.QUEUED.value
                        ),
                        started_at=None,
                        finished_at=now if skipped else None,
                        error=None,
                        log="",
                        events=[],
                    ),
                )
            session.commit()
            self._run_id = run_id
        except Exception:
            session.rollback()
            _LOGGER.exception("agent run open failed agent_id=%s", agent_id)
        finally:
            session.close()

    def begin(self, task_id: str) -> None:
        """Mark one task running and start a fresh log buffer."""
        self._task_id = task_id
        self._lines = []
        self._events = []
        now: datetime = utc_now()

        def change(session: Session) -> None:
            task: AgentOperatorRunTaskRow | None = _task_row(session, self._run_id, task_id)
            if task is None:
                return
            task.state = OperatorTaskState.RUNNING.value
            task.started_at = now
            task.finished_at = None
            task.error = None
            task.log = ""
            task.events = []

        self._mutate(change)

    def end(self, task_id: str, error: str | None) -> None:
        """Store the task outcome and the log collected since ``begin``."""
        state: OperatorTaskState = (
            OperatorTaskState.FAILED if error is not None else OperatorTaskState.SUCCEEDED
        )
        now: datetime = utc_now()
        lines: list[str] = list(self._lines)
        events: list[dict[str, object]] = list(self._events)
        clipped: str | None = _clip(error, _MAX_ERROR)

        def change(session: Session) -> None:
            task: AgentOperatorRunTaskRow | None = _task_row(session, self._run_id, task_id)
            if task is None:
                return
            task.state = state.value
            task.finished_at = now
            task.error = clipped
            task.log = _join_log(lines)
            task.events = events[-_EVENT_LIMIT:]

        self._mutate(change)
        if self._task_id == task_id:
            self._task_id = None

    def append_log(self, line: str) -> None:
        """Keep one logger line for the current task."""
        if self._run_id is None:
            return
        cleaned: str = line.replace("\x00", "").strip()
        if cleaned == "":
            return
        if len(cleaned) > _LINE_LIMIT:
            cleaned = cleaned[:_LINE_LIMIT]
        self._lines.append(cleaned)
        self._trim_lines()

    def record_event(self, step: str, title: str, detail: str) -> None:
        """Store one build-trace step and flush it so a poll can see it."""
        if self._run_id is None or self._task_id is None:
            return
        item: dict[str, object] = {
            "at": utc_now().isoformat(),
            "step": step[:64],
            "title": title[:500],
            "detail": detail[:_DETAIL_LIMIT],
        }
        self._events.append(item)
        if len(self._events) > _EVENT_LIMIT:
            self._events = self._events[-_EVENT_LIMIT:]
        self._flush_current()

    def finish(self, status: DefinitionRunStatus, error: str | None) -> None:
        """Close the run. A failed run marks tasks that never started."""
        if self._run_id is None:
            return
        run_id: UUID = self._run_id
        current: str | None = self._task_id
        lines: list[str] = list(self._lines)
        events: list[dict[str, object]] = list(self._events)
        now: datetime = utc_now()
        clipped: str | None = _clip(error, _MAX_ERROR)

        def change(session: Session) -> None:
            run: AgentOperatorRunRow | None = session.get(AgentOperatorRunRow, run_id)
            if run is None:
                return
            tasks: list[AgentOperatorRunTaskRow] = list(
                session.scalars(
                    select(AgentOperatorRunTaskRow).where(AgentOperatorRunTaskRow.run_id == run_id),
                ),
            )
            if status == DefinitionRunStatus.FAILED:
                _fail_open_tasks(tasks, current, lines, events, clipped, now)
            else:
                _close_success(tasks, current, lines, events, now)
            run.status = status.value
            run.error = clipped
            run.finished_at = now

        self._mutate(change)
        self._task_id = None

    def attach(self, logger: logging.Logger) -> None:
        """Copy log records into the current task buffer."""
        if self._handler is not None:
            return
        handler: _RunLogHandler = _RunLogHandler(self)
        self._handler = handler
        package: logging.Logger = logging.getLogger(LOGGER_NAME)
        targets: list[logging.Logger] = [package]
        if logger is not package and not _is_child_logger(logger, package):
            targets.append(logger)
        seen: set[int] = set()
        target: logging.Logger
        for target in targets:
            identity: int = id(target)
            if identity in seen:
                continue
            seen.add(identity)
            target.addHandler(handler)
            self._loggers.append(target)

    def detach(self) -> None:
        """Remove the log handler. Safe to call more than once."""
        if self._handler is None:
            return
        logger: logging.Logger
        for logger in self._loggers:
            logger.removeHandler(self._handler)
        self._loggers = []
        self._handler = None

    def _flush_current(self) -> None:
        task_id: str | None = self._task_id
        if task_id is None:
            return
        lines: list[str] = list(self._lines)
        events: list[dict[str, object]] = list(self._events)

        def change(session: Session) -> None:
            task: AgentOperatorRunTaskRow | None = _task_row(session, self._run_id, task_id)
            if task is None:
                return
            task.log = _join_log(lines)
            task.events = events[-_EVENT_LIMIT:]

        self._mutate(change)

    def _mutate(self, change: Callable[[Session], None]) -> None:
        if self._run_id is None:
            return
        session: Session = self._database.session()
        try:
            change(session)
            session.commit()
        except Exception:
            session.rollback()
            _LOGGER.exception("agent run write failed run_id=%s", self._run_id)
        finally:
            session.close()

    def _trim_lines(self) -> None:
        while len(self._lines) > 1 and _log_length(self._lines) > _LOG_LIMIT:
            self._lines.pop(0)


class AgentRunReader:
    """Reads run history. The caller owns the session."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def list_for_agent(self, agent_id: UUID, limit: int) -> list[AgentRunRead]:
        """Return the newest runs. Task logs are omitted."""
        runs: list[AgentOperatorRunRow] = list(
            self._session.scalars(
                select(AgentOperatorRunRow)
                .where(AgentOperatorRunRow.agent_id == agent_id)
                .order_by(AgentOperatorRunRow.queued_at.desc())
                .limit(limit),
            ),
        )
        if len(runs) == 0:
            return []
        run_ids: list[UUID] = [run.id for run in runs]
        tasks: list[AgentOperatorRunTaskRow] = list(
            self._session.scalars(
                select(AgentOperatorRunTaskRow).where(AgentOperatorRunTaskRow.run_id.in_(run_ids)),
            ),
        )
        grouped: dict[UUID, list[AgentOperatorRunTaskRow]] = {}
        task: AgentOperatorRunTaskRow
        for task in tasks:
            grouped.setdefault(task.run_id, []).append(task)
        found: list[AgentRunRead] = []
        run: AgentOperatorRunRow
        for run in runs:
            found.append(_run_read(run, grouped.get(run.id, [])))
        return found

    def get_task(self, agent_id: UUID, run_id: UUID, task_id: str) -> AgentRunTaskDetail:
        """Return one task, including its log and trace events."""
        if task_id not in TASK_IDS:
            raise EntityNotFoundError("agent run task", task_id)
        run: AgentOperatorRunRow | None = self._session.get(AgentOperatorRunRow, run_id)
        if run is None or run.agent_id != agent_id:
            raise EntityNotFoundError("agent run", str(run_id))
        task: AgentOperatorRunTaskRow | None = self._session.scalar(
            select(AgentOperatorRunTaskRow).where(
                AgentOperatorRunTaskRow.run_id == run_id,
                AgentOperatorRunTaskRow.task_id == task_id,
            ),
        )
        if task is None:
            raise EntityNotFoundError("agent run task", task_id)
        return _task_detail(run, task)


@contextmanager
def agent_run_log(recorder: AgentRunRecorder) -> Iterator[None]:
    """Bind the recorder that build-trace steps should copy into."""
    token: Token[AgentRunRecorder | None] = _current_run.set(recorder)
    try:
        yield
    finally:
        _current_run.reset(token)


def publish_run_event(step: str, title: str, detail: str) -> None:
    """Copy one build-trace step onto the active agent run. Failures stay local."""
    recorder: AgentRunRecorder | None = _current_run.get()
    if recorder is None:
        return
    try:
        recorder.record_event(step, title, detail)
    except Exception:
        _LOGGER.exception("agent run event failed")


class _RunLogHandler(logging.Handler):
    """Sends formatted records to the recorder buffer."""

    def __init__(self, recorder: AgentRunRecorder) -> None:
        super().__init__()
        self._recorder: AgentRunRecorder = recorder
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """Store one line. A format error does not propagate."""
        try:
            self._recorder.append_log(self.format(record))
        except Exception:
            return


def _count_runs(session: Session, agent_id: UUID) -> int:
    rows: list[UUID] = list(
        session.scalars(
            select(AgentOperatorRunRow.id).where(AgentOperatorRunRow.agent_id == agent_id),
        ),
    )
    return len(rows)


def _task_row(
    session: Session,
    run_id: UUID | None,
    task_id: str,
) -> AgentOperatorRunTaskRow | None:
    if run_id is None:
        return None
    return session.scalar(
        select(AgentOperatorRunTaskRow).where(
            AgentOperatorRunTaskRow.run_id == run_id,
            AgentOperatorRunTaskRow.task_id == task_id,
        ),
    )


def _fail_open_tasks(
    tasks: list[AgentOperatorRunTaskRow],
    current: str | None,
    lines: list[str],
    events: list[dict[str, object]],
    error: str | None,
    now: datetime,
) -> None:
    started: bool = any(
        task.state
        in {
            OperatorTaskState.RUNNING.value,
            OperatorTaskState.SUCCEEDED.value,
            OperatorTaskState.FAILED.value,
        }
        for task in tasks
    )
    if not started:
        failed_one: bool = False
        task_id: str
        for task_id in TASK_IDS:
            task: AgentOperatorRunTaskRow | None = _find_task(tasks, task_id)
            if task is None or task.state != OperatorTaskState.QUEUED.value:
                continue
            if not failed_one:
                _mark(
                    task,
                    OperatorTaskState.FAILED,
                    error,
                    now,
                    lines,
                    events,
                    force_log=True,
                )
                failed_one = True
            else:
                task.state = OperatorTaskState.UPSTREAM_FAILED.value
                task.finished_at = now
        return
    task_row: AgentOperatorRunTaskRow
    for task_row in tasks:
        if task_row.state == OperatorTaskState.RUNNING.value:
            _mark(
                task_row,
                OperatorTaskState.FAILED,
                error,
                now,
                lines if task_row.task_id == current else [],
                events if task_row.task_id == current else [],
                force_log=task_row.task_id == current,
            )
        elif task_row.state == OperatorTaskState.QUEUED.value:
            task_row.state = OperatorTaskState.UPSTREAM_FAILED.value
            task_row.finished_at = now


def _close_success(
    tasks: list[AgentOperatorRunTaskRow],
    current: str | None,
    lines: list[str],
    events: list[dict[str, object]],
    now: datetime,
) -> None:
    task: AgentOperatorRunTaskRow
    for task in tasks:
        if task.state == OperatorTaskState.RUNNING.value and task.task_id == current:
            _mark(task, OperatorTaskState.SUCCEEDED, None, now, lines, events, force_log=True)
        elif task.state == OperatorTaskState.QUEUED.value:
            task.state = OperatorTaskState.SKIPPED.value
            task.finished_at = now


def _mark(
    task: AgentOperatorRunTaskRow,
    state: OperatorTaskState,
    error: str | None,
    now: datetime,
    lines: list[str],
    events: list[dict[str, object]],
    *,
    force_log: bool,
) -> None:
    task.state = state.value
    task.finished_at = now
    task.error = error
    if task.started_at is None:
        task.started_at = now
    if force_log:
        task.log = _join_log(lines)
        task.events = events[-_EVENT_LIMIT:]


def _find_task(
    tasks: list[AgentOperatorRunTaskRow],
    task_id: str,
) -> AgentOperatorRunTaskRow | None:
    task: AgentOperatorRunTaskRow
    for task in tasks:
        if task.task_id == task_id:
            return task
    return None


def _run_read(run: AgentOperatorRunRow, tasks: list[AgentOperatorRunTaskRow]) -> AgentRunRead:
    ordered: list[AgentOperatorRunTaskRow] = _ordered(tasks)
    summaries: list[AgentRunTaskSummary] = [_summary(task) for task in ordered]
    return AgentRunRead(
        id=run.id,
        agent_id=run.agent_id,
        catalog_job_id=run.catalog_job_id,
        status=DefinitionRunStatus(run.status),
        trigger=OperatorRunTrigger(run.trigger),
        try_number=run.try_number,
        operator_type=run.operator_type,
        operator_params=_params(run.operator_params),
        error=run.error,
        queued_at=_required_time(run.queued_at),
        started_at=_aware(run.started_at),
        finished_at=_aware(run.finished_at),
        duration=_duration(run.started_at, run.finished_at),
        tasks=summaries,
    )


def _task_detail(run: AgentOperatorRunRow, task: AgentOperatorRunTaskRow) -> AgentRunTaskDetail:
    return AgentRunTaskDetail(
        run_id=run.id,
        task_id=task.task_id,
        state=OperatorTaskState(task.state),
        started_at=_aware(task.started_at),
        finished_at=_aware(task.finished_at),
        duration=_duration(task.started_at, task.finished_at),
        error=task.error,
        log=task.log,
        events=_events(task.events),
        trigger=OperatorRunTrigger(run.trigger),
        try_number=run.try_number,
        operator_type=run.operator_type,
        operator_params=_params(run.operator_params),
        run_status=DefinitionRunStatus(run.status),
        run_error=run.error,
    )


def _summary(task: AgentOperatorRunTaskRow) -> AgentRunTaskSummary:
    return AgentRunTaskSummary(
        task_id=task.task_id,
        state=OperatorTaskState(task.state),
        started_at=_aware(task.started_at),
        finished_at=_aware(task.finished_at),
        duration=_duration(task.started_at, task.finished_at),
        error=task.error,
    )


def _ordered(tasks: list[AgentOperatorRunTaskRow]) -> list[AgentOperatorRunTaskRow]:
    rank: dict[str, int] = {task_id: index for index, task_id in enumerate(TASK_IDS)}
    return sorted(tasks, key=lambda task: rank.get(task.task_id, len(TASK_IDS)))


def _params(raw: object) -> dict[str, JsonValue]:
    if not isinstance(raw, dict):
        return {}
    stored: dict[str, JsonValue] = {}
    key: object
    value: object
    for key, value in raw.items():
        stored[str(key)] = _json_value(value)
    return stored


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        items: list[JsonValue] = []
        item: object
        for item in value:
            items.append(_json_value(item))
        return items
    if isinstance(value, dict):
        nested: dict[str, JsonValue] = {}
        key: object
        raw: object
        for key, raw in value.items():
            nested[str(key)] = _json_value(raw)
        return nested
    return str(value)


def _events(raw: object) -> list[AgentRunEventRead]:
    if not isinstance(raw, list):
        return []
    found: list[AgentRunEventRead] = []
    item: object
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            found.append(AgentRunEventRead.model_validate(item))
        except ValidationError:
            continue
    return found


def _duration(started: datetime | None, finished: datetime | None) -> float | None:
    start: datetime | None = _aware(started)
    end: datetime | None = _aware(finished)
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _required_time(value: datetime) -> datetime:
    aware: datetime | None = _aware(value)
    if aware is None:
        return value
    return aware


def _join_log(lines: list[str]) -> str:
    text: str = "\n".join(lines)
    if len(text) <= _LOG_LIMIT:
        return text
    return text[-_LOG_LIMIT:]


def _log_length(lines: list[str]) -> int:
    if len(lines) == 0:
        return 0
    return sum(len(line) for line in lines) + len(lines) - 1


def _clip(message: str | None, limit: int) -> str | None:
    if message is None or len(message) <= limit:
        return message
    return message[:limit]


def _is_child_logger(logger: logging.Logger, parent: logging.Logger) -> bool:
    return logger.name.startswith(f"{parent.name}.")
