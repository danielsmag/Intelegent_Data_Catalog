"""In-process job queue. One build runs per subject."""

import logging
import threading
from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.models import (
    ContextGenerationJobRead,
    KnowledgeBuildJobRead,
    KnowledgeBuildState,
    ScopeBuildJobRead,
)
from intelegent_data_catalog.knowledge.models import (
    ContextGenerationOptions,
    ContextSubjectKind,
    ScopeContextOptions,
    TableContextOptions,
)

_LOGGER: logging.Logger = logging.getLogger(LOGGER_NAME)

type ReadJob[ReadT] = Callable[
    [UUID, UUID, str, str, KnowledgeBuildState, str | None],
    ReadT,
]


class BuildWork[OptionsT](Protocol):
    """Stores one built context. The queue does not open a database session."""

    def run(self, subject_id: UUID, options: OptionsT) -> None:
        """Build and save the context. Raise ``CatalogError`` for a domain failure."""
        ...


class KnowledgeBuildWork(Protocol):
    """Stores one table context. The queue does not open a database session."""

    def run(self, subject_id: UUID, options: TableContextOptions) -> None:
        """Build and save the context. Raise ``CatalogError`` for a domain failure."""
        ...


class ScopeBuildWork(Protocol):
    """Stores one scope context. The queue does not open a database session."""

    def run(self, subject_id: UUID, options: ScopeContextOptions) -> None:
        """Build and save the context. Raise ``CatalogError`` for a domain failure."""
        ...


class _StoredJob[ReadT]:
    """Mutable job record. Callers copy it under the queue lock."""

    def __init__(
        self,
        job_id: UUID,
        subject_id: UUID,
        subject_name: str,
        lane: str,
        read_job: ReadJob[ReadT],
    ) -> None:
        self.id: UUID = job_id
        self.subject_id: UUID = subject_id
        self.subject_name: str = subject_name
        self.lane: str = lane
        self.status: KnowledgeBuildState = "running"
        self.error: str | None = None
        self._read_job: ReadJob[ReadT] = read_job

    def read(self) -> ReadT:
        return self._read_job(
            self.id,
            self.subject_id,
            self.subject_name,
            self.lane,
            self.status,
            self.error,
        )


class BuildQueue[OptionsT, ReadT]:
    """Runs at most one build per subject. A later start joins the running job."""

    def __init__(
        self,
        work: BuildWork[OptionsT],
        read_job: ReadJob[ReadT],
        *,
        thread_prefix: str,
        failure_log: str,
        failure_message: str,
    ) -> None:
        self._work: BuildWork[OptionsT] = work
        self._read_job: ReadJob[ReadT] = read_job
        self._thread_prefix: str = thread_prefix
        self._failure_log: str = failure_log
        self._failure_message: str = failure_message
        self._lock: threading.Lock = threading.Lock()
        self._jobs: dict[UUID, _StoredJob[ReadT]] = {}
        self._running: dict[tuple[UUID, str], UUID] = {}

    def start(
        self,
        subject_id: UUID,
        subject_name: str,
        options: OptionsT,
        *,
        lane: str = "",
    ) -> ReadT:
        """Return the running job, or start one and return it immediately."""
        with self._lock:
            key: tuple[UUID, str] = (subject_id, lane)
            current_id: UUID | None = self._running.get(key)
            if current_id is not None:
                current: _StoredJob[ReadT] | None = self._jobs.get(current_id)
                if current is not None and current.status == "running":
                    return current.read()
            created: _StoredJob[ReadT] = _StoredJob(
                uuid4(),
                subject_id,
                subject_name,
                lane,
                self._read_job,
            )
            self._jobs[created.id] = created
            self._running[key] = created.id
            job_id: UUID = created.id
            snapshot: ReadT = created.read()
        worker: threading.Thread = threading.Thread(
            target=self._execute,
            args=(job_id, subject_id, options),
            name=f"{self._thread_prefix}-{job_id}",
            daemon=True,
        )
        worker.start()
        return snapshot

    def get(self, job_id: UUID) -> ReadT | None:
        """Return one job, or none when the id is unknown."""
        with self._lock:
            job: _StoredJob[ReadT] | None = self._jobs.get(job_id)
            if job is None:
                return None
            return job.read()

    def latest(self, subject_id: UUID, *, lane: str = "") -> ReadT | None:
        """Return the newest job for a subject and lane, including one that already finished."""
        with self._lock:
            key: tuple[UUID, str] = (subject_id, lane)
            job_id: UUID | None = self._running.get(key)
            if job_id is None:
                job_id = self._latest_finished(subject_id, lane)
            if job_id is None:
                return None
            job: _StoredJob[ReadT] | None = self._jobs.get(job_id)
            if job is None:
                return None
            return job.read()

    def _latest_finished(self, subject_id: UUID, lane: str) -> UUID | None:
        found: UUID | None = None
        job: _StoredJob[ReadT]
        for job in self._jobs.values():
            if job.subject_id == subject_id and job.lane == lane:
                found = job.id
        return found

    def _execute(self, job_id: UUID, subject_id: UUID, options: OptionsT) -> None:
        try:
            self._work.run(subject_id, options)
        except CatalogError as err:
            self._complete(job_id, "failed", str(err))
        except Exception:
            _LOGGER.exception(self._failure_log)
            self._complete(job_id, "failed", self._failure_message)
        else:
            self._complete(job_id, "succeeded", None)

    def _complete(self, job_id: UUID, status: KnowledgeBuildState, error: str | None) -> None:
        with self._lock:
            job: _StoredJob[ReadT] | None = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return
            job.status = status
            job.error = error
            key: tuple[UUID, str] = (job.subject_id, job.lane)
            current: UUID | None = self._running.get(key)
            if current == job_id:
                self._running.pop(key)


def _read_table_job(
    job_id: UUID,
    subject_id: UUID,
    subject_name: str,
    lane: str,
    status: KnowledgeBuildState,
    error: str | None,
) -> KnowledgeBuildJobRead:
    del lane
    return KnowledgeBuildJobRead(
        id=job_id,
        table_id=subject_id,
        table_name=subject_name,
        status=status,
        error=error,
    )


def _read_scope_job(
    job_id: UUID,
    subject_id: UUID,
    subject_name: str,
    lane: str,
    status: KnowledgeBuildState,
    error: str | None,
) -> ScopeBuildJobRead:
    del lane
    return ScopeBuildJobRead(
        id=job_id,
        scope_id=subject_id,
        scope_name=subject_name,
        status=status,
        error=error,
    )


def _read_context_job(
    job_id: UUID,
    subject_id: UUID,
    subject_name: str,
    lane: str,
    status: KnowledgeBuildState,
    error: str | None,
) -> ContextGenerationJobRead:
    return ContextGenerationJobRead(
        id=job_id,
        subject_kind=_subject_kind(lane),
        subject_id=subject_id,
        subject_name=subject_name,
        status=status,
        error=error,
    )


def _subject_kind(lane: str) -> ContextSubjectKind:
    match lane:
        case "scope":
            return "scope"
        case "table":
            return "table"
        case "table_columns":
            return "table_columns"
        case "column":
            return "column"
        case _:
            raise ValueError(f"unknown context lane: {lane}")


class KnowledgeBuildQueue(BuildQueue[TableContextOptions, KnowledgeBuildJobRead]):
    """Runs at most one context build per table. A later start joins the running job."""

    def __init__(self, work: KnowledgeBuildWork) -> None:
        super().__init__(
            work,
            _read_table_job,
            thread_prefix="knowledge-build",
            failure_log="knowledge context build failed",
            failure_message="context build failed",
        )


class ScopeBuildQueue(BuildQueue[ScopeContextOptions, ScopeBuildJobRead]):
    """Runs at most one context build per scope. A later start joins the running job."""

    def __init__(self, work: ScopeBuildWork) -> None:
        super().__init__(
            work,
            _read_scope_job,
            thread_prefix="scope-build",
            failure_log="scope context build failed",
            failure_message="scope context build failed",
        )


class ContextGenerationWork(Protocol):
    """Stores generated business context. The queue does not open a database session."""

    def run(self, subject_id: UUID, options: ContextGenerationOptions) -> None:
        """Generate and save context. Raise ``CatalogError`` for a domain failure."""
        ...


class ContextGenerationQueue(BuildQueue[ContextGenerationOptions, ContextGenerationJobRead]):
    """Runs at most one context generation per subject and kind."""

    def __init__(self, work: ContextGenerationWork) -> None:
        super().__init__(
            work,
            _read_context_job,
            thread_prefix="context-generation",
            failure_log="context generation failed",
            failure_message="context generation failed",
        )
