

from typing import cast
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Select

from intelegent_data_catalog.exceptions import CatalogError, ConflictError, ValidationAppError
from intelegent_data_catalog.jobs.models import (
    AirflowBuildJobRead,
    ContextGenerationJobRead,
    KnowledgeBuildJobRead,
    KnowledgeBuildState,
    ScopeBuildJobRead,
    SpaceBuildJobRead,
)
from intelegent_data_catalog.jobs.publisher import BuildJobPublisher
from intelegent_data_catalog.knowledge.models import (
    AirflowContextOptions,
    ContextGenerationOptions,
    ContextSubjectKind,
    ScopeContextOptions,
    SpaceContextOptions,
    TableContextOptions,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import CatalogJobKind, CatalogJobStatus
from intelegent_data_catalog.persistence.models import CatalogJobRow

_QUEUE_FAILURE: str = "catalog job could not be queued"


class CatalogJobStore:
    """Reads and writes ``catalog_jobs``. Each call opens its own session."""

    def __init__(self, database: CatalogDatabase) -> None:
        self._database: CatalogDatabase = database

    def running(self, kind: CatalogJobKind, subject_id: UUID, lane: str) -> CatalogJobRow | None:
        """Return the running job for one subject and lane."""
        session: Session = self._database.session()
        try:
            statement: Select[tuple[CatalogJobRow]] = (
                select(CatalogJobRow)
                .where(
                    CatalogJobRow.kind == kind.value,
                    CatalogJobRow.subject_id == subject_id,
                    CatalogJobRow.lane == lane,
                    CatalogJobRow.status == CatalogJobStatus.RUNNING.value,
                )
                .limit(1)
            )
            row: CatalogJobRow | None = session.scalar(statement)
            if row is not None:
                session.expunge(row)
            return row
        finally:
            session.close()

    def add_running(
        self,
        kind: CatalogJobKind,
        subject_id: UUID,
        subject_name: str,
        lane: str,
        options: BaseModel,
    ) -> tuple[CatalogJobRow, bool]:
        """Insert a running job. The flag is false when an existing run is joined."""
        current: CatalogJobRow | None = self.running(kind, subject_id, lane)
        if current is not None:
            return current, False
        session: Session = self._database.session()
        stored: CatalogJobRow | None = None
        try:
            row: CatalogJobRow = CatalogJobRow(
                id=uuid4(),
                kind=kind.value,
                subject_id=subject_id,
                subject_name=subject_name,
                lane=lane,
                status=CatalogJobStatus.RUNNING.value,
                error=None,
                options=_dump_options(options),
                created_at=utc_now(),
                finished_at=None,
            )
            session.add(row)
            session.commit()
            session.expunge(row)
            stored = row
        except IntegrityError:
            session.rollback()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        if stored is not None:
            return stored, True
        raced: CatalogJobRow | None = self.running(kind, subject_id, lane)
        if raced is None:
            raise ConflictError("catalog job could not be started")
        return raced, False

    def get(self, job_id: UUID) -> CatalogJobRow | None:
        """Return one job."""
        session: Session = self._database.session()
        try:
            row: CatalogJobRow | None = session.get(CatalogJobRow, job_id)
            if row is not None:
                session.expunge(row)
            return row
        finally:
            session.close()

    def latest(self, kind: CatalogJobKind, subject_id: UUID, lane: str) -> CatalogJobRow | None:
        """Return the newest job for a subject and lane."""
        session: Session = self._database.session()
        try:
            statement: Select[tuple[CatalogJobRow]] = (
                select(CatalogJobRow)
                .where(
                    CatalogJobRow.kind == kind.value,
                    CatalogJobRow.subject_id == subject_id,
                    CatalogJobRow.lane == lane,
                )
                .order_by(CatalogJobRow.created_at.desc(), CatalogJobRow.id.desc())
                .limit(1)
            )
            row: CatalogJobRow | None = session.scalar(statement)
            if row is not None:
                session.expunge(row)
            return row
        finally:
            session.close()

    def complete(self, job_id: UUID, status: KnowledgeBuildState, error: str | None) -> None:
        """Mark a running job finished. A second completion is ignored."""
        session: Session = self._database.session()
        try:
            row: CatalogJobRow | None = session.get(CatalogJobRow, job_id)
            if row is None or row.status != CatalogJobStatus.RUNNING.value:
                return
            row.status = status
            row.error = error
            row.finished_at = utc_now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class CatalogJobs:
    """Starts builds and reads their Postgres status. The worker performs the build."""

    def __init__(self, database: CatalogDatabase, publisher: BuildJobPublisher) -> None:
        self._store: CatalogJobStore = CatalogJobStore(database)
        self._publisher: BuildJobPublisher = publisher

    def start_knowledge(
        self,
        subject_id: UUID,
        subject_name: str,
        options: TableContextOptions,
    ) -> KnowledgeBuildJobRead:
        """Start a table context build, or join the one already running."""
        row: CatalogJobRow = self._start(
            CatalogJobKind.KNOWLEDGE,
            subject_id,
            subject_name,
            "",
            options,
        )
        return _knowledge_read(row)

    def get_knowledge(self, job_id: UUID) -> KnowledgeBuildJobRead | None:
        """Return one table build."""
        return _knowledge_read_optional(self._typed(job_id, CatalogJobKind.KNOWLEDGE))

    def latest_knowledge(self, subject_id: UUID) -> KnowledgeBuildJobRead | None:
        """Return the newest table build."""
        row: CatalogJobRow | None = self._store.latest(CatalogJobKind.KNOWLEDGE, subject_id, "")
        return _knowledge_read_optional(row)

    def start_scope(
        self,
        subject_id: UUID,
        subject_name: str,
        options: ScopeContextOptions,
    ) -> ScopeBuildJobRead:
        """Start a scope context build, or join the one already running."""
        row: CatalogJobRow = self._start(
            CatalogJobKind.SCOPE,
            subject_id,
            subject_name,
            "",
            options,
        )
        return _scope_read(row)

    def get_scope(self, job_id: UUID) -> ScopeBuildJobRead | None:
        """Return one scope build."""
        return _scope_read_optional(self._typed(job_id, CatalogJobKind.SCOPE))

    def latest_scope(self, subject_id: UUID) -> ScopeBuildJobRead | None:
        """Return the newest scope build."""
        return _scope_read_optional(self._store.latest(CatalogJobKind.SCOPE, subject_id, ""))

    def start_space(
        self,
        subject_id: UUID,
        subject_name: str,
        options: SpaceContextOptions,
    ) -> SpaceBuildJobRead:
        """Start a space context build, or join the one already running."""
        row: CatalogJobRow = self._start(
            CatalogJobKind.SPACE,
            subject_id,
            subject_name,
            "",
            options,
        )
        return _space_read(row)

    def get_space(self, job_id: UUID) -> SpaceBuildJobRead | None:
        """Return one space build."""
        return _space_read_optional(self._typed(job_id, CatalogJobKind.SPACE))

    def latest_space(self, subject_id: UUID) -> SpaceBuildJobRead | None:
        """Return the newest space build."""
        return _space_read_optional(self._store.latest(CatalogJobKind.SPACE, subject_id, ""))

    def start_airflow(
        self,
        subject_id: UUID,
        subject_name: str,
        options: AirflowContextOptions,
    ) -> AirflowBuildJobRead:
        """Start an Airflow instance build, or join the one already running."""
        row: CatalogJobRow = self._start(
            CatalogJobKind.AIRFLOW,
            subject_id,
            subject_name,
            "",
            options,
        )
        return _airflow_read(row)

    def get_airflow(self, job_id: UUID) -> AirflowBuildJobRead | None:
        """Return one Airflow build."""
        return _airflow_read_optional(self._typed(job_id, CatalogJobKind.AIRFLOW))

    def latest_airflow(self, subject_id: UUID) -> AirflowBuildJobRead | None:
        """Return the newest Airflow build."""
        return _airflow_read_optional(self._store.latest(CatalogJobKind.AIRFLOW, subject_id, ""))

    def start_context(
        self,
        subject_id: UUID,
        subject_name: str,
        options: ContextGenerationOptions,
        *,
        lane: str,
    ) -> ContextGenerationJobRead:
        """Start a context write, or join the one already running for this lane."""
        row: CatalogJobRow = self._start(
            CatalogJobKind.CONTEXT,
            subject_id,
            subject_name,
            lane,
            options,
        )
        return _context_read(row)

    def get_context(self, job_id: UUID) -> ContextGenerationJobRead | None:
        """Return one context generation."""
        return _context_read_optional(self._typed(job_id, CatalogJobKind.CONTEXT))

    def latest_context(self, subject_id: UUID, lane: str) -> ContextGenerationJobRead | None:
        """Return the newest context generation for a subject and lane."""
        return _context_read_optional(self._store.latest(CatalogJobKind.CONTEXT, subject_id, lane))

    def _start(
        self,
        kind: CatalogJobKind,
        subject_id: UUID,
        subject_name: str,
        lane: str,
        options: BaseModel,
    ) -> CatalogJobRow:
        row: CatalogJobRow
        created: bool
        row, created = self._store.add_running(kind, subject_id, subject_name, lane, options)
        if not created:
            return row
        try:
            self._publisher.enqueue_build(row.id)
        except Exception as err:
            self._store.complete(row.id, "failed", _QUEUE_FAILURE)
            raise CatalogError(_QUEUE_FAILURE) from err
        return self._require(row.id)

    def _typed(self, job_id: UUID, kind: CatalogJobKind) -> CatalogJobRow | None:
        row: CatalogJobRow | None = self._store.get(job_id)
        if row is None or row.kind != kind.value:
            return None
        return row

    def _require(self, job_id: UUID) -> CatalogJobRow:
        row: CatalogJobRow | None = self._store.get(job_id)
        if row is None:
            raise ConflictError("catalog job could not be started")
        return row


def _dump_options(options: BaseModel) -> dict[str, object]:
    dumped: object = options.model_dump(mode="json")
    if not isinstance(dumped, dict):
        raise ValidationAppError("catalog job options must be an object")
    return cast(dict[str, object], dumped)


def _job_status(value: str) -> KnowledgeBuildState:
    match value:
        case "running" | "succeeded" | "failed":
            return value
        case _:
            raise ValidationAppError(f"unknown catalog job status: {value}")


def _subject_kind(lane: str) -> ContextSubjectKind:
    match lane:
        case "space" | "scope" | "table" | "table_columns" | "column" | "airflow":
            return lane
        case _:
            raise ValidationAppError(f"unknown context lane: {lane}")


def _knowledge_read(row: CatalogJobRow) -> KnowledgeBuildJobRead:
    return KnowledgeBuildJobRead(
        id=row.id,
        table_id=row.subject_id,
        table_name=row.subject_name,
        status=_job_status(row.status),
        error=row.error,
    )


def _knowledge_read_optional(row: CatalogJobRow | None) -> KnowledgeBuildJobRead | None:
    if row is None:
        return None
    return _knowledge_read(row)


def _scope_read(row: CatalogJobRow) -> ScopeBuildJobRead:
    return ScopeBuildJobRead(
        id=row.id,
        scope_id=row.subject_id,
        scope_name=row.subject_name,
        status=_job_status(row.status),
        error=row.error,
    )


def _scope_read_optional(row: CatalogJobRow | None) -> ScopeBuildJobRead | None:
    if row is None:
        return None
    return _scope_read(row)


def _space_read(row: CatalogJobRow) -> SpaceBuildJobRead:
    return SpaceBuildJobRead(
        id=row.id,
        space_id=row.subject_id,
        space_name=row.subject_name,
        status=_job_status(row.status),
        error=row.error,
    )


def _space_read_optional(row: CatalogJobRow | None) -> SpaceBuildJobRead | None:
    if row is None:
        return None
    return _space_read(row)


def _airflow_read(row: CatalogJobRow) -> AirflowBuildJobRead:
    return AirflowBuildJobRead(
        id=row.id,
        instance_id=row.subject_id,
        instance_name=row.subject_name,
        status=_job_status(row.status),
        error=row.error,
    )


def _airflow_read_optional(row: CatalogJobRow | None) -> AirflowBuildJobRead | None:
    if row is None:
        return None
    return _airflow_read(row)


def _context_read(row: CatalogJobRow) -> ContextGenerationJobRead:
    return ContextGenerationJobRead(
        id=row.id,
        subject_kind=_subject_kind(row.lane),
        subject_id=row.subject_id,
        subject_name=row.subject_name,
        status=_job_status(row.status),
        error=row.error,
    )


def _context_read_optional(row: CatalogJobRow | None) -> ContextGenerationJobRead | None:
    if row is None:
        return None
    return _context_read(row)
