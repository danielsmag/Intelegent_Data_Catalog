"""Data source writes and reads."""

from uuid import UUID

from intelegent_data_catalog.exceptions import ConflictError, ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import DataSourceRow
from intelegent_data_catalog.registry.reads import data_source_read, dump_json_dict
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
)
from intelegent_data_catalog.services.support import require_row, required


class DataSourceService:
    """Data source persistence against the shared catalog session."""

    def __init__(self, repo: RegistryRepository) -> None:
        self._repo: RegistryRepository = repo

    def create_data_source(self, body: DataSourceCreate) -> DataSourceRead:
        """Register a data source without provisioning a connector."""
        row: DataSourceRow = self._repo.add_data_source(
            name=body.name,
            source_type=body.type.value,
            configuration=dump_json_dict(body.configuration),
            credential_ref=body.credential_ref,
        )
        self._repo.commit()
        return data_source_read(row)

    def list_data_sources(self) -> list[DataSourceRead]:
        """List data sources."""
        return [data_source_read(row) for row in self._repo.list_data_sources()]

    def get_data_source(self, data_source_id: UUID) -> DataSourceRead:
        """Return one data source."""
        return data_source_read(self._data_source(data_source_id))

    def update_data_source(
        self,
        data_source_id: UUID,
        patch: DataSourceUpdate,
    ) -> DataSourceRead:
        """Patch source name, configuration, or the secret reference."""
        row: DataSourceRow = self._data_source(data_source_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = required(patch.name, "name")
        if "configuration" in changes:
            if patch.configuration is None:
                raise ValidationAppError("configuration cannot be null", field="configuration")
            row.configuration = dump_json_dict(patch.configuration)
        if "credential_ref" in changes:
            row.credential_ref = patch.credential_ref
        row.updated_at = utc_now()
        self._repo.commit()
        return data_source_read(row)

    def delete_data_source(self, data_source_id: UUID) -> None:
        """Delete a data source that has no tables or bindings."""
        row: DataSourceRow = self._data_source(data_source_id)
        if self._repo.count_tables_for_source(data_source_id) > 0:
            raise ConflictError("data source still has tables")
        if self._repo.count_bindings_for_source(data_source_id) > 0:
            raise ConflictError("data source still has source bindings")
        self._repo.delete(row)
        self._repo.commit()

    def _data_source(self, data_source_id: UUID) -> DataSourceRow:
        row: DataSourceRow | None = self._repo.get_data_source(data_source_id)
        return require_row(row, "data source", data_source_id)
