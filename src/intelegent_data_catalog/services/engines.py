"""Engine connection writes and reads."""

from datetime import datetime
from uuid import UUID, uuid4

from intelegent_data_catalog.exceptions import ConflictError, ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import EngineConnectionRow
from intelegent_data_catalog.registry.reads import dump_json_dict, engine_connection_read
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    EngineConnectionCreate,
    EngineConnectionRead,
    EngineConnectionUpdate,
)
from intelegent_data_catalog.services.support import require_row, required


class EngineService:
    """Engine connection persistence against the shared catalog session."""

    def __init__(self, repo: RegistryRepository) -> None:
        self._repo: RegistryRepository = repo

    def create_engine_connection(self, body: EngineConnectionCreate) -> EngineConnectionRead:
        """Register an engine endpoint."""
        now: datetime = utc_now()
        row: EngineConnectionRow = EngineConnectionRow(
            id=uuid4(),
            name=body.name,
            engine_type=body.engine_type.value,
            endpoint=body.endpoint,
            credential_ref=body.credential_ref,
            configuration=dump_json_dict(body.configuration),
            status=body.status.value,
            created_at=now,
            updated_at=now,
        )
        stored: EngineConnectionRow = self._repo.add_engine_connection(row)
        self._repo.commit()
        return engine_connection_read(stored)

    def list_engine_connections(self) -> list[EngineConnectionRead]:
        """List engine connections."""
        return [engine_connection_read(row) for row in self._repo.list_engine_connections()]

    def get_engine_connection(self, engine_connection_id: UUID) -> EngineConnectionRead:
        """Return one engine connection."""
        return engine_connection_read(self._engine(engine_connection_id))

    def update_engine_connection(
        self,
        engine_connection_id: UUID,
        patch: EngineConnectionUpdate,
    ) -> EngineConnectionRead:
        """Patch an engine connection. Engine type stays fixed."""
        row: EngineConnectionRow = self._engine(engine_connection_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = required(patch.name, "name")
        if "endpoint" in changes:
            row.endpoint = required(patch.endpoint, "endpoint")
        if "credential_ref" in changes:
            row.credential_ref = patch.credential_ref
        if "configuration" in changes:
            if patch.configuration is None:
                raise ValidationAppError("configuration cannot be null", field="configuration")
            row.configuration = dump_json_dict(patch.configuration)
        if "status" in changes:
            row.status = required(patch.status, "status").value
        row.updated_at = utc_now()
        self._repo.commit()
        return engine_connection_read(row)

    def delete_engine_connection(self, engine_connection_id: UUID) -> None:
        """Delete an engine connection that has no source bindings."""
        row: EngineConnectionRow = self._engine(engine_connection_id)
        if self._repo.count_source_bindings_for_engine(engine_connection_id) > 0:
            raise ConflictError("engine connection still has source bindings")
        self._repo.delete(row)
        self._repo.commit()

    def _engine(self, engine_connection_id: UUID) -> EngineConnectionRow:
        row: EngineConnectionRow | None = self._repo.get_engine_connection(engine_connection_id)
        return require_row(row, "engine connection", engine_connection_id)
