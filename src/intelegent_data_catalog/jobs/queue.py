"""In-process job queue. One knowledge-context build runs per table."""

import logging
import threading
from typing import Protocol
from uuid import UUID, uuid4

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.models import KnowledgeBuildJobRead, KnowledgeBuildState
from intelegent_data_catalog.knowledge.models import TableContextOptions

_LOGGER: logging.Logger = logging.getLogger(LOGGER_NAME)
_FAILED: str = "context build failed"


class KnowledgeBuildWork(Protocol):
    """Stores one table context. The queue does not open a database session."""

    def run(self, table_id: UUID, options: TableContextOptions) -> None:
        """Build and save the context. Raise ``CatalogError`` for a domain failure."""
        ...


class _StoredJob:
    """Mutable job record. Callers copy it under the queue lock."""

    def __init__(self, job_id: UUID, table_id: UUID, table_name: str) -> None:
        self.id: UUID = job_id
        self.table_id: UUID = table_id
        self.table_name: str = table_name
        self.status: KnowledgeBuildState = "running"
        self.error: str | None = None

    def read(self) -> KnowledgeBuildJobRead:
        return KnowledgeBuildJobRead(
            id=self.id,
            table_id=self.table_id,
            table_name=self.table_name,
            status=self.status,
            error=self.error,
        )


class KnowledgeBuildQueue:
    """Runs at most one context build per table. A later start joins the running job."""

    def __init__(self, work: KnowledgeBuildWork) -> None:
        self._work: KnowledgeBuildWork = work
        self._lock: threading.Lock = threading.Lock()
        self._jobs: dict[UUID, _StoredJob] = {}
        self._running: dict[UUID, UUID] = {}

    def start(
        self,
        table_id: UUID,
        table_name: str,
        options: TableContextOptions,
    ) -> KnowledgeBuildJobRead:
        """Return the running job, or start one and return it immediately."""
        with self._lock:
            current_id: UUID | None = self._running.get(table_id)
            if current_id is not None:
                current: _StoredJob | None = self._jobs.get(current_id)
                if current is not None and current.status == "running":
                    return current.read()
            created: _StoredJob = _StoredJob(uuid4(), table_id, table_name)
            self._jobs[created.id] = created
            self._running[table_id] = created.id
            snapshot: KnowledgeBuildJobRead = created.read()
        worker: threading.Thread = threading.Thread(
            target=self._execute,
            args=(snapshot.id, table_id, options),
            name=f"knowledge-build-{snapshot.id}",
            daemon=True,
        )
        worker.start()
        return snapshot

    def get(self, job_id: UUID) -> KnowledgeBuildJobRead | None:
        """Return one job, or none when the id is unknown."""
        with self._lock:
            job: _StoredJob | None = self._jobs.get(job_id)
            if job is None:
                return None
            return job.read()

    def latest(self, table_id: UUID) -> KnowledgeBuildJobRead | None:
        """Return the newest job for a table, including one that already finished."""
        with self._lock:
            job_id: UUID | None = self._running.get(table_id)
            if job_id is None:
                job_id = self._latest_finished(table_id)
            if job_id is None:
                return None
            job: _StoredJob | None = self._jobs.get(job_id)
            if job is None:
                return None
            return job.read()

    def _latest_finished(self, table_id: UUID) -> UUID | None:
        found: UUID | None = None
        job: _StoredJob
        for job in self._jobs.values():
            if job.table_id == table_id:
                found = job.id
        return found

    def _execute(self, job_id: UUID, table_id: UUID, options: TableContextOptions) -> None:
        try:
            self._work.run(table_id, options)
        except CatalogError as err:
            self._complete(job_id, "failed", str(err))
        except Exception:
            _LOGGER.exception("knowledge context build failed")
            self._complete(job_id, "failed", _FAILED)
        else:
            self._complete(job_id, "succeeded", None)

    def _complete(self, job_id: UUID, status: KnowledgeBuildState, error: str | None) -> None:
        with self._lock:
            job: _StoredJob | None = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return
            job.status = status
            job.error = error
            current: UUID | None = self._running.get(job.table_id)
            if current == job_id:
                self._running.pop(job.table_id)
