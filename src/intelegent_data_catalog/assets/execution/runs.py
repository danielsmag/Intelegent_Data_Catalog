"""Persist and read asset operator runs on a session the operator cannot roll back."""

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

from intelegent_data_catalog.assets.execution.models import (
    AssetRunEventRead,
    AssetRunRead,
    AssetRunTaskDetail,
    AssetRunTaskSummary,
)
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.jobs.trace import publish_build_step
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    DefinitionRunStatus,
    OperatorRunTrigger,
    OperatorTaskState,
)
from intelegent_data_catalog.persistence.models import AssetRunRow, AssetRunTaskRow

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.assets")
_MAX_ERROR: int = 8000
_LOG_LIMIT: int = 100_000
_LINE_LIMIT: int = 8_000
_EVENT_LIMIT: int = 200

_current_run: ContextVar[AssetRunRecorder | None] = ContextVar("asset_run_log", default=None)


class AssetRunRecorder:
    """Writes one run on its own sessions. A log failure does not fail the operator."""

    def __init__(self, database: CatalogDatabase) -> None:
        self._database: CatalogDatabase = database
        self._run_id: UUID | None = None
        self._task_id: str | None = None
        self._task_order: tuple[str, ...] = ()
        self._lines: list[str] = []
        self._handler: logging.Handler | None = None
        self._loggers: list[logging.Logger] = []

    @property
    def run_id(self) -> UUID | None:
        """Return the open run, or none when the row could not be stored."""
        return self._run_id

    def open(
        self,
        *,
        asset_id: UUID,
        item_id: UUID | None,
        trigger: OperatorRunTrigger,
        force: bool,
        operator_params: Mapping[str, JsonValue],
        task_ids: tuple[str, ...],
    ) -> None:
        """Insert the run and one queued task per operator."""
        if self._run_id is not None:
            return
        self._task_order = task_ids
        session: Session = self._database.session()
        try:
            prior: int = len(
                list(
                    session.scalars(select(AssetRunRow.id).where(AssetRunRow.asset_id == asset_id)),
                ),
            )
            now: datetime = utc_now()
            run_id: UUID = uuid4()
            params: dict[str, object] = {key: value for key, value in operator_params.items()}
            session.add(
                AssetRunRow(
                    id=run_id,
                    asset_id=asset_id,
                    item_id=item_id,
                    status=DefinitionRunStatus.RUNNING.value,
                    trigger=trigger.value,
                    force=force,
                    try_number=prior + 1,
                    operator_params=params,
                    error=None,
                    queued_at=now,
                    started_at=now,
                    finished_at=None,
                ),
            )
            task_id: str
            for task_id in task_ids:
                session.add(
                    AssetRunTaskRow(
                        id=uuid4(),
                        run_id=run_id,
                        task_id=task_id,
                        state=OperatorTaskState.QUEUED.value,
                        started_at=None,
                        finished_at=None,
                        error=None,
                        log="",
                        events=[],
                    ),
                )
            session.commit()
            self._run_id = run_id
        except Exception:
            session.rollback()
            _LOGGER.exception("asset run open failed asset_id=%s", asset_id)
        finally:
            session.close()

    def begin(self, task_id: str) -> None:
        """Mark one task running and start a fresh log buffer."""
        self._task_id = task_id
        self._lines = []
        now: datetime = utc_now()

        def change(session: Session) -> None:
            task: AssetRunTaskRow | None = _task_row(session, self._run_id, task_id)
            if task is None:
                return
            task.state = OperatorTaskState.RUNNING.value
            task.started_at = now
            task.finished_at = None
            task.error = None
            task.log = ""

        self._mutate(change)
        publish_build_step("", "note", _task_label(task_id))

    def end(self, task_id: str, error: str | None) -> None:
        """Store the task outcome and the log collected since ``begin``."""
        state: OperatorTaskState = (
            OperatorTaskState.FAILED if error is not None else OperatorTaskState.SUCCEEDED
        )
        self._close(task_id, state, error)
        if error is not None:
            publish_build_step("", "note", f"{_task_label(task_id)}\n{error}")

    def skip(self, task_id: str, reason: str) -> None:
        """Mark the task skipped and keep the reason in the log."""
        self._lines.append(reason)
        self._close(task_id, OperatorTaskState.SKIPPED, None)
        publish_build_step("", "note", f"{_task_label(task_id)}\n{reason}")

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

    def finish(self, status: DefinitionRunStatus, error: str | None) -> None:
        """Close the run. A failed run marks tasks that never started."""
        if self._run_id is None:
            return
        run_id: UUID = self._run_id
        now: datetime = utc_now()
        clipped: str | None = _clip(error)

        def change(session: Session) -> None:
            run: AssetRunRow | None = session.get(AssetRunRow, run_id)
            if run is None:
                return
            if status == DefinitionRunStatus.FAILED:
                task: AssetRunTaskRow
                for task in session.scalars(
                    select(AssetRunTaskRow).where(AssetRunTaskRow.run_id == run_id),
                ):
                    if task.state == OperatorTaskState.QUEUED.value:
                        task.state = OperatorTaskState.UPSTREAM_FAILED.value
                        task.finished_at = now
                    elif task.state == OperatorTaskState.RUNNING.value:
                        task.state = OperatorTaskState.FAILED.value
                        task.error = clipped
                        task.finished_at = now
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
        logger.addHandler(handler)
        self._loggers.append(logger)

    def detach(self) -> None:
        """Remove the log handler. Safe to call more than once."""
        if self._handler is None:
            return
        logger: logging.Logger
        for logger in self._loggers:
            logger.removeHandler(self._handler)
        self._loggers = []
        self._handler = None

    def _close(self, task_id: str, state: OperatorTaskState, error: str | None) -> None:
        now: datetime = utc_now()
        lines: list[str] = list(self._lines)
        clipped: str | None = _clip(error)

        def change(session: Session) -> None:
            task: AssetRunTaskRow | None = _task_row(session, self._run_id, task_id)
            if task is None:
                return
            task.state = state.value
            task.finished_at = now
            task.error = clipped
            task.log = _join_log(lines)

        self._mutate(change)
        if self._task_id == task_id:
            self._task_id = None

    def _mutate(self, change: Callable[[Session], None]) -> None:
        if self._run_id is None:
            return
        session: Session = self._database.session()
        try:
            change(session)
            session.commit()
        except Exception:
            session.rollback()
            _LOGGER.exception("asset run write failed run_id=%s", self._run_id)
        finally:
            session.close()


class AssetRunReader:
    """Reads run history. The caller owns the session."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def list_for_asset(self, asset_id: UUID, limit: int) -> list[AssetRunRead]:
        """Return the newest runs. Task logs are omitted."""
        runs: list[AssetRunRow] = list(
            self._session.scalars(
                select(AssetRunRow)
                .where(AssetRunRow.asset_id == asset_id)
                .order_by(AssetRunRow.queued_at.desc())
                .limit(limit),
            ),
        )
        if len(runs) == 0:
            return []
        run_ids: list[UUID] = [run.id for run in runs]
        tasks: list[AssetRunTaskRow] = list(
            self._session.scalars(
                select(AssetRunTaskRow).where(AssetRunTaskRow.run_id.in_(run_ids)),
            ),
        )
        grouped: dict[UUID, list[AssetRunTaskRow]] = {}
        task: AssetRunTaskRow
        for task in tasks:
            grouped.setdefault(task.run_id, []).append(task)
        return [_run_read(run, grouped.get(run.id, [])) for run in runs]

    def get_task(self, asset_id: UUID, run_id: UUID, task_id: str) -> AssetRunTaskDetail:
        """Return one task, including its log."""
        run: AssetRunRow | None = self._session.get(AssetRunRow, run_id)
        if run is None or run.asset_id != asset_id:
            raise EntityNotFoundError("asset run", str(run_id))
        task: AssetRunTaskRow | None = self._session.scalar(
            select(AssetRunTaskRow).where(
                AssetRunTaskRow.run_id == run_id,
                AssetRunTaskRow.task_id == task_id,
            ),
        )
        if task is None:
            raise EntityNotFoundError("asset run task", task_id)
        return _task_detail(run, task)


@contextmanager
def asset_run_log(recorder: AssetRunRecorder) -> Iterator[None]:
    """Bind the recorder for the current operator run."""
    token: Token[AssetRunRecorder | None] = _current_run.set(recorder)
    try:
        yield
    finally:
        _current_run.reset(token)


class _RunLogHandler(logging.Handler):
    """Sends formatted records to the recorder buffer."""

    def __init__(self, recorder: AssetRunRecorder) -> None:
        super().__init__()
        self._recorder: AssetRunRecorder = recorder
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """Store one line. A format error does not propagate."""
        try:
            self._recorder.append_log(self.format(record))
        except Exception:
            return


def _task_label(task_id: str) -> str:
    """Turn ``iceberg_table_metadata`` into a stream step title."""
    label: str = task_id.replace("_", " ").strip()
    if label == "":
        return "Step"
    return label[:1].upper() + label[1:]


def _task_row(session: Session, run_id: UUID | None, task_id: str) -> AssetRunTaskRow | None:
    if run_id is None:
        return None
    return session.scalar(
        select(AssetRunTaskRow).where(
            AssetRunTaskRow.run_id == run_id,
            AssetRunTaskRow.task_id == task_id,
        ),
    )


def _run_read(run: AssetRunRow, tasks: list[AssetRunTaskRow]) -> AssetRunRead:
    ordered: list[AssetRunTaskRow] = sorted(tasks, key=lambda task: task.task_id)
    return AssetRunRead(
        id=run.id,
        asset_id=run.asset_id,
        item_id=run.item_id,
        status=DefinitionRunStatus(run.status),
        trigger=OperatorRunTrigger(run.trigger),
        force=run.force,
        try_number=run.try_number,
        operator_params=_params(run.operator_params),
        error=run.error,
        queued_at=_required_time(run.queued_at),
        started_at=_aware(run.started_at),
        finished_at=_aware(run.finished_at),
        duration=_duration(run.started_at, run.finished_at),
        tasks=[_summary(task) for task in ordered],
    )


def _task_detail(run: AssetRunRow, task: AssetRunTaskRow) -> AssetRunTaskDetail:
    return AssetRunTaskDetail(
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
        operator_params=_params(run.operator_params),
        run_status=DefinitionRunStatus(run.status),
        run_error=run.error,
    )


def _summary(task: AssetRunTaskRow) -> AssetRunTaskSummary:
    return AssetRunTaskSummary(
        task_id=task.task_id,
        state=OperatorTaskState(task.state),
        started_at=_aware(task.started_at),
        finished_at=_aware(task.finished_at),
        duration=_duration(task.started_at, task.finished_at),
        error=task.error,
    )


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
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        nested: dict[str, JsonValue] = {}
        key: object
        raw: object
        for key, raw in value.items():
            nested[str(key)] = _json_value(raw)
        return nested
    return str(value)


def _events(raw: object) -> list[AssetRunEventRead]:
    if not isinstance(raw, list):
        return []
    found: list[AssetRunEventRead] = []
    item: object
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            found.append(AssetRunEventRead.model_validate(item))
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


def _clip(message: str | None) -> str | None:
    if message is None or len(message) <= _MAX_ERROR:
        return message
    return message[:_MAX_ERROR]
