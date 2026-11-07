

import logging
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Select

from intelegent_data_catalog.assets.models import AssetContextOptions
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import CatalogError, ConflictError, ValidationAppError
from intelegent_data_catalog.jobs.cancel import CANCELLED_JOB_ERROR, CancelSignal
from intelegent_data_catalog.jobs.models import (
    AirflowBuildJobRead,
    AssetBuildJobRead,
    CatalogJobCancelRead,
    ContextGenerationJobRead,
    KnowledgeBuildJobRead,
    KnowledgeBuildState,
)
from intelegent_data_catalog.jobs.publisher import BuildJobPublisher
from intelegent_data_catalog.jobs.trace import BuildTraceSink, TraceStep, close_build_trace
from intelegent_data_catalog.knowledge.models import (
    AirflowContextOptions,
    ContextGenerationOptions,
    ContextSubjectKind,
    TableContextOptions,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import CatalogJobKind, CatalogJobStatus
from intelegent_data_catalog.persistence.models import CatalogJobRow

_LOGGER: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.jobs")
_QUEUE_FAILURE: str = "catalog job could not be queued"


class CatalogJobStore:
    """Reads and writes ``catalog_jobs``. Each call opens its own session."""

    def __init__(self, database: CatalogDatabase) -> None:
        self._database: CatalogDatabase = database

    def running(self, kind: CatalogJobKind, subject_id: UUID, lane: str) -> CatalogJobRow | None:
        """Return the running job for one subject and lane."""
        session: Session = self._database.session()
        try:
            statement: Any = (
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
            statement: Select[CatalogJobRow] = (
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

    def list_running(self) -> list[CatalogJobRow]:
        """Return every job still marked running."""
        session: Session = self._database.session()
        try:
            statement: Select[CatalogJobRow] = select(CatalogJobRow).where(
                CatalogJobRow.status == CatalogJobStatus.RUNNING.value,
            )
            rows: list[CatalogJobRow] = list(session.scalars(statement))
            row: CatalogJobRow
            for row in rows:
                session.expunge(row)
            return rows
        finally:
            session.close()

    def statuses(self, job_ids: set[UUID]) -> list[CatalogJobRow]:
        """Return the rows for these jobs. Missing ids are omitted."""
        if len(job_ids) == 0:
            return []
        session: Session = self._database.session()
        try:
            statement: Select[CatalogJobRow] = select(CatalogJobRow).where(
                CatalogJobRow.id.in_(job_ids),
            )
            rows: list[CatalogJobRow] = list(session.scalars(statement))
            row: CatalogJobRow
            for row in rows:
                session.expunge(row)
            return rows
        finally:
            session.close()

    def complete(self, job_id: UUID, status: KnowledgeBuildState, error: str | None) -> bool:
        """Mark a running job finished. A second completion is ignored."""
        session: Session = self._database.session()
        try:
            row: CatalogJobRow | None = session.get(CatalogJobRow, job_id)
            if row is None or row.status != CatalogJobStatus.RUNNING.value:
                return False
            row.status = status
            row.error = error
            row.finished_at = utc_now()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return True


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







    def start_asset(
        self,
        subject_id: UUID,
        subject_name: str,
        options: AssetContextOptions,
    ) -> AssetBuildJobRead:
        """Start an asset context build, or join the one already running."""
        row: CatalogJobRow = self._start(
            CatalogJobKind.ASSET,
            subject_id,
            subject_name,
            "",
            options,
        )
        return _asset_read(row)

    def get_asset(self, job_id: UUID) -> AssetBuildJobRead | None:
        """Return one asset build."""
        return _asset_read_optional(self._typed(job_id, CatalogJobKind.ASSET))

    def latest_asset(self, subject_id: UUID) -> AssetBuildJobRead | None:
        """Return the newest asset build."""
        return _asset_read_optional(self._store.latest(CatalogJobKind.ASSET, subject_id, ""))

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

    def cancel(
        self,
        job_id: UUID,
        signal: CancelSignal,
        signal_ttl_seconds: int,
        trace: BuildTraceSink | None = None,
    ) -> CatalogJobCancelRead | None:
        """Stop one running build.

        The flag is written before the row changes so a live worker exits. The
        row is marked failed even when no worker holds it, which releases the
        UI and lets the next build start. A finished job is returned unchanged.
        """
        row: CatalogJobRow | None = self._store.get(job_id)
        if row is None:
            return None
        if row.status != CatalogJobStatus.RUNNING.value:
            return _cancel_read(row)
        try:
            signal.request(job_id, signal_ttl_seconds)
        except Exception:
            _LOGGER.exception("catalog job cancel signal failed job_id=%s", job_id)
        wrote: bool = self._store.complete(job_id, "failed", CANCELLED_JOB_ERROR)
        if wrote and trace is not None:
            _close_trace(trace, row, "failed", CANCELLED_JOB_ERROR)
        updated: CatalogJobRow | None = self._store.get(job_id)
        if updated is None:
            return None
        return _cancel_read(updated)

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


def _close_trace(sink: BuildTraceSink, row: CatalogJobRow, step: TraceStep, detail: str) -> None:
    """Publish a terminal trace step. A broker error does not undo the row."""
    try:
        close_build_trace(
            sink,
            job_id=row.id,
            kind=row.kind,
            subject_id=row.subject_id,
            subject_name=row.subject_name,
            options=row.options,
            step=step,
            detail=detail,
        )
    except Exception:
        _LOGGER.exception("catalog job trace close failed job_id=%s", row.id)


def _cancel_read(row: CatalogJobRow) -> CatalogJobCancelRead:
    return CatalogJobCancelRead(
        id=row.id,
        status=_job_status(row.status),
        error=row.error,
    )


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
        case "table" | "table_columns" | "column" | "airflow" | "asset":
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










def _asset_read(row: CatalogJobRow) -> AssetBuildJobRead:
    return AssetBuildJobRead(
        id=row.id,
        asset_id=row.subject_id,
        asset_name=row.subject_name,
        status=_job_status(row.status),
        error=row.error,
    )


def _asset_read_optional(row: CatalogJobRow | None) -> AssetBuildJobRead | None:
    if row is None:
        return None
    return _asset_read(row)


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
