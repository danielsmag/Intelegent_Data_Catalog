"""Per-target sync intervals. Beat claims due rows and enqueues one sync each."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.jobs.publisher import SyncJobPublisher
from intelegent_data_catalog.jobs.schemas import SyncScheduleRead, SyncScheduleWrite
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    AirflowInstanceStatus,
    EngineType,
    SyncRunStatus,
)
from intelegent_data_catalog.persistence.models import (
    AirflowInstanceRow,
    AirflowSyncRunRow,
    MetadataSyncRunRow,
    SourceEngineBindingRow,
    SyncScheduleRow,
)


class SyncScheduleService:
    """Stores and claims sync schedules. The caller owns the session."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def get_binding(self, source_binding_id: UUID) -> SyncScheduleRead:
        """Return the schedule for one Trino source binding."""
        self._require_trino_binding(source_binding_id)
        return _read(self._require_binding_schedule(source_binding_id))

    def put_binding(self, source_binding_id: UUID, body: SyncScheduleWrite) -> SyncScheduleRead:
        """Create or replace the schedule for one Trino source binding."""
        self._require_trino_binding(source_binding_id)
        row: SyncScheduleRow | None = self._find_binding_schedule(source_binding_id)
        stored: SyncScheduleRow = self._save_binding(row, source_binding_id, body)
        self._session.commit()
        return _read(stored)

    def delete_binding(self, source_binding_id: UUID) -> None:
        """Remove the schedule for one Trino source binding."""
        self._require_trino_binding(source_binding_id)
        self._session.delete(self._require_binding_schedule(source_binding_id))
        self._session.commit()

    def get_airflow(self, instance_id: UUID) -> SyncScheduleRead:
        """Return the schedule for one Airflow instance."""
        self._require_instance(instance_id)
        return _read(self._require_instance_schedule(instance_id))

    def put_airflow(self, instance_id: UUID, body: SyncScheduleWrite) -> SyncScheduleRead:
        """Create or replace the schedule for one Airflow instance."""
        self._require_instance(instance_id)
        row: SyncScheduleRow | None = self._find_instance_schedule(instance_id)
        stored: SyncScheduleRow = self._save_instance(row, instance_id, body)
        self._session.commit()
        return _read(stored)

    def delete_airflow(self, instance_id: UUID) -> None:
        """Remove the schedule for one Airflow instance."""
        self._require_instance(instance_id)
        self._session.delete(self._require_instance_schedule(instance_id))
        self._session.commit()

    def claim_due(self, now: datetime, publisher: SyncJobPublisher) -> int:
        """Enqueue due schedules and move their next run forward.

        A target with a running sync, a disabled Airflow instance, or a non-Trino
        binding stays due so a later tick can try again.
        """
        statement = select(SyncScheduleRow).where(
            SyncScheduleRow.enabled.is_(True),
            SyncScheduleRow.next_run_at <= now,
        )
        if self._session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows: list[SyncScheduleRow] = list(self._session.scalars(statement))
        claimed: int = 0
        row: SyncScheduleRow
        for row in rows:
            if not self._ready(row):
                continue
            self._enqueue(row, publisher)
            row.next_run_at = now + timedelta(seconds=row.interval_seconds)
            row.updated_at = now
            claimed += 1
        self._session.commit()
        return claimed

    def _ready(self, row: SyncScheduleRow) -> bool:
        binding_id: UUID | None = row.source_engine_binding_id
        if binding_id is not None:
            binding: SourceEngineBindingRow | None = self._session.get(
                SourceEngineBindingRow,
                binding_id,
            )
            if binding is None or binding.engine_type != EngineType.TRINO.value:
                return False
            return not self._trino_running(binding_id)
        instance_id: UUID | None = row.airflow_instance_id
        if instance_id is None:
            return False
        instance: AirflowInstanceRow | None = self._session.get(AirflowInstanceRow, instance_id)
        if instance is None or instance.status != AirflowInstanceStatus.ACTIVE.value:
            return False
        return not self._airflow_running(instance_id)

    def _enqueue(self, row: SyncScheduleRow, publisher: SyncJobPublisher) -> None:
        binding_id: UUID | None = row.source_engine_binding_id
        if binding_id is not None:
            publisher.enqueue_trino_sync(binding_id)
            return
        instance_id: UUID | None = row.airflow_instance_id
        if instance_id is None:
            raise ValidationAppError("sync schedule has no target")
        publisher.enqueue_airflow_sync(instance_id)

    def _trino_running(self, source_binding_id: UUID) -> bool:
        statement = (
            select(MetadataSyncRunRow.id)
            .where(
                MetadataSyncRunRow.source_engine_binding_id == source_binding_id,
                MetadataSyncRunRow.status == SyncRunStatus.RUNNING.value,
            )
            .limit(1)
        )
        return self._session.scalar(statement) is not None

    def _airflow_running(self, instance_id: UUID) -> bool:
        statement = (
            select(AirflowSyncRunRow.id)
            .where(
                AirflowSyncRunRow.instance_id == instance_id,
                AirflowSyncRunRow.status == SyncRunStatus.RUNNING.value,
            )
            .limit(1)
        )
        return self._session.scalar(statement) is not None

    def _save_binding(
        self,
        row: SyncScheduleRow | None,
        source_binding_id: UUID,
        body: SyncScheduleWrite,
    ) -> SyncScheduleRow:
        now: datetime = utc_now()
        if row is None:
            created: SyncScheduleRow = SyncScheduleRow(
                id=uuid4(),
                source_engine_binding_id=source_binding_id,
                airflow_instance_id=None,
                interval_seconds=body.interval_seconds,
                enabled=body.enabled,
                next_run_at=now,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
            return created
        row.interval_seconds = body.interval_seconds
        row.enabled = body.enabled
        row.updated_at = now
        if body.enabled:
            row.next_run_at = now + timedelta(seconds=body.interval_seconds)
        return row

    def _save_instance(
        self,
        row: SyncScheduleRow | None,
        instance_id: UUID,
        body: SyncScheduleWrite,
    ) -> SyncScheduleRow:
        now: datetime = utc_now()
        if row is None:
            created: SyncScheduleRow = SyncScheduleRow(
                id=uuid4(),
                source_engine_binding_id=None,
                airflow_instance_id=instance_id,
                interval_seconds=body.interval_seconds,
                enabled=body.enabled,
                next_run_at=now,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
            return created
        row.interval_seconds = body.interval_seconds
        row.enabled = body.enabled
        row.updated_at = now
        if body.enabled:
            row.next_run_at = now + timedelta(seconds=body.interval_seconds)
        return row

    def _require_trino_binding(self, source_binding_id: UUID) -> SourceEngineBindingRow:
        row: SourceEngineBindingRow | None = self._session.get(
            SourceEngineBindingRow,
            source_binding_id,
        )
        if row is None:
            raise EntityNotFoundError("source binding", str(source_binding_id))
        if row.engine_type != EngineType.TRINO.value:
            raise ValidationAppError(
                "metadata sync schedules attach to a TRINO source binding",
                field="source_engine_binding_id",
            )
        return row

    def _require_instance(self, instance_id: UUID) -> AirflowInstanceRow:
        row: AirflowInstanceRow | None = self._session.get(AirflowInstanceRow, instance_id)
        if row is None:
            raise EntityNotFoundError("airflow instance", str(instance_id))
        return row

    def _find_binding_schedule(self, source_binding_id: UUID) -> SyncScheduleRow | None:
        statement = select(SyncScheduleRow).where(
            SyncScheduleRow.source_engine_binding_id == source_binding_id,
        )
        return self._session.scalar(statement)

    def _find_instance_schedule(self, instance_id: UUID) -> SyncScheduleRow | None:
        statement = select(SyncScheduleRow).where(
            SyncScheduleRow.airflow_instance_id == instance_id,
        )
        return self._session.scalar(statement)

    def _require_binding_schedule(self, source_binding_id: UUID) -> SyncScheduleRow:
        row: SyncScheduleRow | None = self._find_binding_schedule(source_binding_id)
        if row is None:
            raise EntityNotFoundError("sync schedule", str(source_binding_id))
        return row

    def _require_instance_schedule(self, instance_id: UUID) -> SyncScheduleRow:
        row: SyncScheduleRow | None = self._find_instance_schedule(instance_id)
        if row is None:
            raise EntityNotFoundError("sync schedule", str(instance_id))
        return row


def _read(row: SyncScheduleRow) -> SyncScheduleRead:
    return SyncScheduleRead(
        id=row.id,
        source_engine_binding_id=row.source_engine_binding_id,
        airflow_instance_id=row.airflow_instance_id,
        interval_seconds=row.interval_seconds,
        enabled=row.enabled,
        next_run_at=row.next_run_at,
    )
