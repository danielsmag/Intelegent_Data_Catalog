"""Metadata sync run writes and reads."""

from datetime import datetime
from uuid import UUID, uuid4

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import EngineType, SyncRunStatus
from intelegent_data_catalog.persistence.models import MetadataSyncRunRow, SourceEngineBindingRow
from intelegent_data_catalog.registry.reads import dump_json_dict, dump_model, sync_run_read
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import SyncRunCreate, SyncRunRead, SyncRunUpdate
from intelegent_data_catalog.services.support import require_row, required

_TERMINAL_SYNC: frozenset[SyncRunStatus] = frozenset(
    {
        SyncRunStatus.SUCCEEDED,
        SyncRunStatus.PARTIAL,
        SyncRunStatus.FAILED,
    }
)


class SyncRunService:
    """Sync run persistence against the shared catalog session."""

    def __init__(self, repo: RegistryRepository) -> None:
        self._repo: RegistryRepository = repo

    def create_sync_run(self, body: SyncRunCreate) -> SyncRunRead:
        """Record a sync run for a Trino source binding."""
        binding: SourceEngineBindingRow = self._source_binding(body.source_engine_binding_id)
        if binding.engine_type != EngineType.TRINO.value:
            raise ValidationAppError(
                "metadata sync runs attach to a TRINO source binding",
                field="source_engine_binding_id",
            )
        finished_at: datetime | None = _sync_finished_at(body.status, body.finished_at)
        now: datetime = utc_now()
        row: MetadataSyncRunRow = MetadataSyncRunRow(
            id=uuid4(),
            source_engine_binding_id=body.source_engine_binding_id,
            started_at=body.started_at or now,
            finished_at=finished_at,
            status=body.status.value,
            scan_scope=dump_model(body.scan_scope),
            counts=dump_model(body.counts),
            sanitized_error=body.sanitized_error,
            checkpoint=None if body.checkpoint is None else dump_json_dict(body.checkpoint),
        )
        stored: MetadataSyncRunRow = self._repo.add_sync_run(row)
        self._repo.commit()
        return sync_run_read(stored)

    def list_sync_runs(self, source_engine_binding_id: UUID | None) -> list[SyncRunRead]:
        """List recorded sync runs."""
        if source_engine_binding_id is not None:
            self._source_binding(source_engine_binding_id)
        return [sync_run_read(row) for row in self._repo.list_sync_runs(source_engine_binding_id)]

    def get_sync_run(self, sync_run_id: UUID) -> SyncRunRead:
        """Return one sync run."""
        return sync_run_read(self._sync_run(sync_run_id))

    def update_sync_run(self, sync_run_id: UUID, patch: SyncRunUpdate) -> SyncRunRead:
        """Update a recorded sync run. Finished runs keep their status."""
        row: MetadataSyncRunRow = self._sync_run(sync_run_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        current: SyncRunStatus = SyncRunStatus(row.status)
        new_status: SyncRunStatus = current
        if "status" in changes:
            new_status = required(patch.status, "status")
            if current in _TERMINAL_SYNC and new_status is not current:
                raise ValidationAppError(
                    "a finished sync run cannot change status",
                    field="status",
                )
            row.status = new_status.value
        if "finished_at" in changes:
            row.finished_at = patch.finished_at
        if new_status is SyncRunStatus.RUNNING and row.finished_at is not None:
            raise ValidationAppError("a running sync has no finished_at", field="finished_at")
        if new_status in _TERMINAL_SYNC and row.finished_at is None:
            row.finished_at = utc_now()
        if "scan_scope" in changes:
            if patch.scan_scope is None:
                raise ValidationAppError("scan_scope cannot be null", field="scan_scope")
            row.scan_scope = dump_model(patch.scan_scope)
        if "counts" in changes:
            if patch.counts is None:
                raise ValidationAppError("counts cannot be null", field="counts")
            row.counts = dump_model(patch.counts)
        if "sanitized_error" in changes:
            row.sanitized_error = patch.sanitized_error
        if "checkpoint" in changes:
            row.checkpoint = None if patch.checkpoint is None else dump_json_dict(patch.checkpoint)
        self._repo.commit()
        return sync_run_read(row)

    def _source_binding(self, source_binding_id: UUID) -> SourceEngineBindingRow:
        row: SourceEngineBindingRow | None = self._repo.get_source_binding(source_binding_id)
        return require_row(row, "source binding", source_binding_id)

    def _sync_run(self, sync_run_id: UUID) -> MetadataSyncRunRow:
        row: MetadataSyncRunRow | None = self._repo.get_sync_run(sync_run_id)
        return require_row(row, "sync run", sync_run_id)


def _sync_finished_at(status: SyncRunStatus, finished_at: datetime | None) -> datetime | None:
    if status is SyncRunStatus.RUNNING and finished_at is not None:
        raise ValidationAppError("a running sync has no finished_at", field="finished_at")
    if status in _TERMINAL_SYNC and finished_at is None:
        return utc_now()
    return finished_at
