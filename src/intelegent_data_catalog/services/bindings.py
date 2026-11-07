"""Source and agent binding writes and reads."""

from datetime import datetime
from uuid import UUID, uuid4

from intelegent_data_catalog.exceptions import ConflictError, ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import BindingRole, EngineType
from intelegent_data_catalog.persistence.models import (
    AgentEngineBindingRow,
    CatalogTableRow,
    DataSourceRow,
    EngineConnectionRow,
    SourceEngineBindingRow,
)
from intelegent_data_catalog.registry.reads import (
    agent_binding_read,
    dump_model,
    source_binding_read,
)
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    AgentBindingCreate,
    AgentBindingRead,
    AgentBindingUpdate,
    SourceBindingCreate,
    SourceBindingRead,
    SourceBindingUpdate,
)
from intelegent_data_catalog.services.support import require_row, required


class BindingService:
    """Engine binding persistence against the shared catalog session."""

    def __init__(self, repo: RegistryRepository) -> None:
        self._repo: RegistryRepository = repo

    def create_source_binding(self, body: SourceBindingCreate) -> SourceBindingRead:
        """Bind a data source to an engine. Engine type is copied from the connection."""
        self._data_source(body.data_source_id)
        engine: EngineConnectionRow = self._engine(body.engine_connection_id)
        self._ensure_binding_key(body.engine_connection_id, body.binding_key, None)
        now: datetime = utc_now()
        row: SourceEngineBindingRow = SourceEngineBindingRow(
            id=uuid4(),
            data_source_id=body.data_source_id,
            engine_connection_id=body.engine_connection_id,
            binding_key=body.binding_key,
            engine_type=engine.engine_type,
            configuration=dump_model(body.configuration),
            capabilities=dump_model(body.capabilities),
            created_at=now,
            updated_at=now,
        )
        stored: SourceEngineBindingRow = self._repo.add_source_binding(row)
        self._repo.commit()
        return source_binding_read(stored)

    def list_source_bindings(
        self,
        data_source_id: UUID | None,
        engine_connection_id: UUID | None,
    ) -> list[SourceBindingRead]:
        """List source bindings."""
        if data_source_id is not None:
            self._data_source(data_source_id)
        if engine_connection_id is not None:
            self._engine(engine_connection_id)
        rows: list[SourceEngineBindingRow] = self._repo.list_source_bindings(
            data_source_id=data_source_id,
            engine_connection_id=engine_connection_id,
        )
        return [source_binding_read(row) for row in rows]

    def get_source_binding(self, source_binding_id: UUID) -> SourceBindingRead:
        """Return one source binding."""
        return source_binding_read(self._source_binding(source_binding_id))

    def update_source_binding(
        self,
        source_binding_id: UUID,
        patch: SourceBindingUpdate,
    ) -> SourceBindingRead:
        """Patch a source binding. Data source and engine stay fixed."""
        row: SourceEngineBindingRow = self._source_binding(source_binding_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "binding_key" in changes:
            binding_key: str = required(patch.binding_key, "binding_key")
            self._ensure_binding_key(row.engine_connection_id, binding_key, row.id)
            row.binding_key = binding_key
        if "configuration" in changes:
            if patch.configuration is None:
                raise ValidationAppError("configuration cannot be null", field="configuration")
            row.configuration = dump_model(patch.configuration)
        if "capabilities" in changes:
            if patch.capabilities is None:
                raise ValidationAppError("capabilities cannot be null", field="capabilities")
            row.capabilities = dump_model(patch.capabilities)
        row.updated_at = utc_now()
        self._repo.commit()
        return source_binding_read(row)

    def delete_source_binding(self, source_binding_id: UUID) -> None:
        """Delete a source binding that has no agent bindings or sync runs."""
        row: SourceEngineBindingRow = self._source_binding(source_binding_id)
        if self._repo.count_agent_bindings_for_source(source_binding_id) > 0:
            raise ConflictError("source binding still has agent bindings")
        if self._repo.count_sync_runs(source_binding_id) > 0:
            raise ConflictError("source binding still has sync runs")
        self._repo.delete(row)
        self._repo.commit()

    def create_agent_binding(
        self,
        table_id: UUID,
        body: AgentBindingCreate,
    ) -> AgentBindingRead:
        """Store an explicit engine address for a table.

        ``engine_ref_key`` is taken from the request. It is not copied from the table
        source key or from a binding on the other engine.
        """
        table: CatalogTableRow = self._table(table_id)
        source_binding: SourceEngineBindingRow = self._source_binding(body.source_engine_binding_id)
        if table.data_source_id != source_binding.data_source_id:
            raise ValidationAppError(
                "agent binding and source binding must use the same data source",
                field="source_engine_binding_id",
            )
        _assert_role_engine(body.role, source_binding.engine_type)
        _assert_selection(body.role, body.is_selected)
        self._ensure_engine_ref_key(source_binding.id, body.engine_ref_key, None)
        if body.is_selected:
            self._ensure_single_selected_query(table_id, None)
        now: datetime = utc_now()
        row: AgentEngineBindingRow = AgentEngineBindingRow(
            id=uuid4(),
            catalog_table_id=table.id,
            data_source_id=table.data_source_id,
            source_engine_binding_id=source_binding.id,
            engine_type=source_binding.engine_type,
            engine_ref=dump_model(body.engine_ref),
            engine_ref_key=body.engine_ref_key,
            role=body.role.value,
            state=body.state.value,
            is_selected=body.is_selected,
            last_validated_at=body.last_validated_at,
            created_at=now,
            updated_at=now,
        )
        stored: AgentEngineBindingRow = self._repo.add_agent_binding(row)
        self._repo.commit()
        return agent_binding_read(stored)

    def list_agent_bindings(self, table_id: UUID) -> list[AgentBindingRead]:
        """List engine addresses stored for a table."""
        self._table(table_id)
        return [agent_binding_read(row) for row in self._repo.list_agent_bindings(table_id)]

    def get_agent_binding(self, binding_id: UUID) -> AgentBindingRead:
        """Return one agent binding."""
        return agent_binding_read(self._agent_binding(binding_id))

    def update_agent_binding(
        self,
        binding_id: UUID,
        patch: AgentBindingUpdate,
    ) -> AgentBindingRead:
        """Patch an agent address. Ref and key must change together."""
        row: AgentEngineBindingRow = self._agent_binding(binding_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        ref_in_patch: bool = "engine_ref" in changes
        key_in_patch: bool = "engine_ref_key" in changes
        if ref_in_patch != key_in_patch:
            raise ValidationAppError(
                "engine_ref and engine_ref_key must be updated together",
                field="engine_ref",
            )
        if ref_in_patch:
            if patch.engine_ref is None:
                raise ValidationAppError("engine_ref cannot be null", field="engine_ref")
            engine_ref_key: str = required(patch.engine_ref_key, "engine_ref_key")
            self._ensure_engine_ref_key(row.source_engine_binding_id, engine_ref_key, row.id)
            row.engine_ref = dump_model(patch.engine_ref)
            row.engine_ref_key = engine_ref_key
        if "state" in changes:
            row.state = required(patch.state, "state").value
        if "is_selected" in changes:
            is_selected: bool = required(patch.is_selected, "is_selected")
            role: BindingRole = BindingRole(row.role)
            _assert_selection(role, is_selected)
            if is_selected:
                self._ensure_single_selected_query(row.catalog_table_id, row.id)
            row.is_selected = is_selected
        if "last_validated_at" in changes:
            row.last_validated_at = patch.last_validated_at
        row.updated_at = utc_now()
        self._repo.commit()
        return agent_binding_read(row)

    def delete_agent_binding(self, binding_id: UUID) -> None:
        """Delete one agent binding."""
        row: AgentEngineBindingRow = self._agent_binding(binding_id)
        self._repo.delete(row)
        self._repo.commit()

    def _ensure_binding_key(
        self,
        engine_connection_id: UUID,
        binding_key: str,
        current_id: UUID | None,
    ) -> None:
        existing: SourceEngineBindingRow | None = self._repo.find_source_binding_by_key(
            engine_connection_id,
            binding_key,
        )
        if existing is not None and existing.id != current_id:
            raise ConflictError("binding_key already exists for this engine connection")

    def _ensure_engine_ref_key(
        self,
        source_binding_id: UUID,
        engine_ref_key: str,
        current_id: UUID | None,
    ) -> None:
        existing: AgentEngineBindingRow | None = self._repo.find_agent_binding_by_key(
            source_binding_id,
            engine_ref_key,
        )
        if existing is not None and existing.id != current_id:
            raise ConflictError("engine_ref_key already exists for this source binding")

    def _ensure_single_selected_query(self, table_id: UUID, current_id: UUID | None) -> None:
        existing: AgentEngineBindingRow | None = self._repo.find_selected_query(table_id)
        if existing is not None and existing.id != current_id:
            raise ConflictError("a selected QUERY binding already exists for this table")

    def _data_source(self, data_source_id: UUID) -> DataSourceRow:
        row: DataSourceRow | None = self._repo.get_data_source(data_source_id)
        return require_row(row, "data source", data_source_id)

    def _engine(self, engine_connection_id: UUID) -> EngineConnectionRow:
        row: EngineConnectionRow | None = self._repo.get_engine_connection(engine_connection_id)
        return require_row(row, "engine connection", engine_connection_id)

    def _table(self, table_id: UUID) -> CatalogTableRow:
        row: CatalogTableRow | None = self._repo.get_table(table_id)
        return require_row(row, "table", table_id)

    def _source_binding(self, source_binding_id: UUID) -> SourceEngineBindingRow:
        row: SourceEngineBindingRow | None = self._repo.get_source_binding(source_binding_id)
        return require_row(row, "source binding", source_binding_id)

    def _agent_binding(self, binding_id: UUID) -> AgentEngineBindingRow:
        row: AgentEngineBindingRow | None = self._repo.get_agent_binding(binding_id)
        return require_row(row, "agent binding", binding_id)


def _assert_role_engine(role: BindingRole, engine_type: str) -> None:
    match role:
        case BindingRole.DISCOVERY:
            if engine_type != EngineType.TRINO.value:
                raise ValidationAppError(
                    "DISCOVERY bindings require a TRINO engine connection",
                    field="role",
                )
        case BindingRole.QUERY:
            if engine_type != EngineType.SPARK_SQL.value:
                raise ValidationAppError(
                    "QUERY bindings require a SPARK_SQL engine connection",
                    field="role",
                )


def _assert_selection(role: BindingRole, is_selected: bool) -> None:
    if is_selected and role is not BindingRole.QUERY:
        raise ValidationAppError("only a QUERY binding can be selected", field="is_selected")
