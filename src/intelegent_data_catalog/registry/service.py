"""Catalog persistence use cases.

User descriptions stay distinct from imported descriptions. Spark addresses are stored
as given and are never derived from Trino addresses.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import (
    ConflictError,
    EntityNotFoundError,
    ValidationAppError,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    BindingRole,
    ContextOrigin,
    EngineType,
    SyncRunStatus,
    TableStatus,
)
from intelegent_data_catalog.persistence.models import (
    AssetEngineBindingRow,
    CatalogTableRow,
    ColumnRow,
    DataSourceRow,
    EngineConnectionRow,
    MetadataSyncRunRow,
    RelationshipColumnMappingRow,
    RelationshipRow,
    ScopeRow,
    SourceEngineBindingRow,
    SpaceRow,
)
from intelegent_data_catalog.registry.identifiers import slugify, source_key_from_parts
from intelegent_data_catalog.registry.reads import (
    asset_binding_read,
    column_read,
    data_source_read,
    dump_json_dict,
    dump_model,
    engine_connection_read,
    relationship_read,
    scope_read,
    source_binding_read,
    space_read,
    sync_run_read,
    table_read,
)
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    AssetBindingCreate,
    AssetBindingRead,
    AssetBindingUpdate,
    CatalogTableCreate,
    CatalogTableRead,
    CatalogTableUpdate,
    ColumnCreate,
    ColumnMappingWrite,
    ColumnObservation,
    ColumnRead,
    ColumnUpdate,
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    EngineConnectionCreate,
    EngineConnectionRead,
    EngineConnectionUpdate,
    RelationshipCreate,
    RelationshipRead,
    RelationshipUpdate,
    ScopeCreate,
    ScopeRead,
    ScopeUpdate,
    SourceBindingCreate,
    SourceBindingRead,
    SourceBindingUpdate,
    SourceSnapshot,
    SpaceCreate,
    SpaceRead,
    SpaceUpdate,
    SyncRunCreate,
    SyncRunRead,
    SyncRunUpdate,
    TableRelationships,
)

_TERMINAL_SYNC: frozenset[SyncRunStatus] = frozenset(
    {
        SyncRunStatus.SUCCEEDED,
        SyncRunStatus.PARTIAL,
        SyncRunStatus.FAILED,
    }
)


class RegistryService:
    """Application catalog writes and reads against PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._repo: RegistryRepository = RegistryRepository(session)

    def create_space(self, body: SpaceCreate) -> SpaceRead:
        """Create a space."""
        slug: str = _slug_for(body.name, body.slug)
        self._ensure_space_slug(slug, None)
        row: SpaceRow = self._repo.add_space(
            name=body.name,
            slug=slug,
            description=body.description,
        )
        self._repo.commit()
        return space_read(row)

    def list_spaces(self) -> list[SpaceRead]:
        """List spaces."""
        return [space_read(row) for row in self._repo.list_spaces()]

    def get_space(self, space_id: UUID) -> SpaceRead:
        """Return one space."""
        return space_read(self._space(space_id))

    def update_space(self, space_id: UUID, patch: SpaceUpdate) -> SpaceRead:
        """Patch a space. An omitted slug is left unchanged."""
        row: SpaceRow = self._space(space_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = _required(patch.name, "name")
        if "slug" in changes:
            slug: str = _required(patch.slug, "slug")
            self._ensure_space_slug(slug, row.id)
            row.slug = slug
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            _apply_user_context(row, patch.context)
        row.updated_at = utc_now()
        self._repo.commit()
        return space_read(row)

    def delete_space(self, space_id: UUID) -> None:
        """Delete a space that has no scopes."""
        row: SpaceRow = self._space(space_id)
        if self._repo.count_scopes(space_id) > 0:
            raise ConflictError("space still has scopes")
        self._repo.delete(row)
        self._repo.commit()

    def create_scope(self, space_id: UUID, body: ScopeCreate) -> ScopeRead:
        """Create a scope in a space."""
        self._space(space_id)
        slug: str = _slug_for(body.name, body.slug)
        self._ensure_scope_slug(space_id, slug, None)
        row: ScopeRow = self._repo.add_scope(
            space_id=space_id,
            name=body.name,
            slug=slug,
            description=body.description,
        )
        self._repo.commit()
        return scope_read(row)

    def list_scopes(self, space_id: UUID) -> list[ScopeRead]:
        """List scopes in a space."""
        self._space(space_id)
        return [scope_read(row) for row in self._repo.list_scopes(space_id)]

    def get_scope(self, scope_id: UUID) -> ScopeRead:
        """Return one scope."""
        return scope_read(self._scope(scope_id))

    def update_scope(self, scope_id: UUID, patch: ScopeUpdate) -> ScopeRead:
        """Patch a scope."""
        row: ScopeRow = self._scope(scope_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = _required(patch.name, "name")
        if "slug" in changes:
            slug: str = _required(patch.slug, "slug")
            self._ensure_scope_slug(row.space_id, slug, row.id)
            row.slug = slug
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            _apply_user_context(row, patch.context)
        row.updated_at = utc_now()
        self._repo.commit()
        return scope_read(row)

    def delete_scope(self, scope_id: UUID) -> None:
        """Delete a scope that has no tables and is not a default scope."""
        row: ScopeRow = self._scope(scope_id)
        if self._repo.count_tables_in_scope(scope_id) > 0:
            raise ConflictError("scope still has tables")
        if self._repo.count_default_scope_bindings(scope_id) > 0:
            raise ConflictError("scope is the default for a source binding")
        self._repo.delete(row)
        self._repo.commit()

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
            row.name = _required(patch.name, "name")
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

    def create_table(self, scope_id: UUID, body: CatalogTableCreate) -> CatalogTableRead:
        """Create a catalog table. Source key case is preserved."""
        self._scope(scope_id)
        self._data_source(body.data_source_id)
        source_key: str = body.source_key or source_key_from_parts(
            body.physical_catalog,
            body.physical_namespace,
            body.physical_table,
        )
        self._ensure_source_key(body.data_source_id, source_key, None)
        if body.source_native_id is not None:
            self._ensure_native_id(body.data_source_id, body.source_native_id, None)
        now: datetime = utc_now()
        row: CatalogTableRow = CatalogTableRow(
            id=uuid4(),
            scope_id=scope_id,
            data_source_id=body.data_source_id,
            name=body.name,
            display_name=body.display_name,
            description=body.description,
            table_type=body.table_type.value,
            status=body.status.value,
            physical_catalog=body.physical_catalog,
            physical_namespace=body.physical_namespace,
            physical_table=body.physical_table,
            source_ref=dump_model(body.source_ref),
            source_key=source_key,
            source_native_id=body.source_native_id,
            table_format=body.table_format,
            source_description=body.source_description,
            row_count=body.row_count,
            created_at=now,
            updated_at=now,
            last_seen_at=None,
            last_synced_at=None,
        )
        stored: CatalogTableRow = self._repo.add_table(row)
        self._repo.commit()
        return table_read(stored)

    def list_tables(self, scope_id: UUID) -> list[CatalogTableRead]:
        """List tables in a scope."""
        self._scope(scope_id)
        return [table_read(row) for row in self._repo.list_tables(scope_id)]

    def get_table(self, table_id: UUID) -> CatalogTableRead:
        """Return one catalog table."""
        return table_read(self._table(table_id))

    def find_table_by_source_key(
        self,
        data_source_id: UUID,
        source_key: str,
    ) -> CatalogTableRead | None:
        """Return the table for a source key, or none when it is not registered."""
        row: CatalogTableRow | None = self._repo.find_table_by_source_key(
            data_source_id,
            source_key,
        )
        if row is None:
            return None
        return table_read(row)

    def update_table(self, table_id: UUID, patch: CatalogTableUpdate) -> CatalogTableRead:
        """Patch user fields and the manual scope assignment."""
        row: CatalogTableRow = self._table(table_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = _required(patch.name, "name")
        if "display_name" in changes:
            row.display_name = patch.display_name
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            _apply_user_context(row, patch.context)
        if "status" in changes:
            row.status = _required(patch.status, "status").value
        if "table_format" in changes:
            row.table_format = patch.table_format
        if "scope_id" in changes:
            scope_id: UUID = _required(patch.scope_id, "scope_id")
            self._scope(scope_id)
            row.scope_id = scope_id
        row.updated_at = utc_now()
        self._repo.commit()
        return table_read(row)

    def delete_table(self, table_id: UUID) -> None:
        """Delete a table that is not part of a relationship."""
        row: CatalogTableRow = self._table(table_id)
        if self._repo.count_relationships_for_table(table_id) > 0:
            raise ConflictError("table is used by a relationship")
        self._repo.delete_asset_bindings_for_table(table_id)
        self._repo.delete_columns_for_table(table_id)
        self._repo.delete(row)
        self._repo.commit()

    def apply_source_snapshot(self, table_id: UUID, snapshot: SourceSnapshot) -> CatalogTableRead:
        """Apply imported fields. User description, scope, and DEPRECATED stay in place."""
        row: CatalogTableRow = self._table(table_id)
        changes: dict[str, object] = snapshot.model_dump(exclude_unset=True)
        if "source_description" in changes:
            row.source_description = snapshot.source_description
        if "row_count" in changes:
            row.row_count = snapshot.row_count
        if "status" in changes and snapshot.status is not None:
            self._apply_snapshot_status(row, snapshot.status)
        observed_at: datetime = utc_now()
        row.last_seen_at = _observed_time(
            changes,
            "last_seen_at",
            snapshot.last_seen_at,
            observed_at,
        )
        row.last_synced_at = _observed_time(
            changes,
            "last_synced_at",
            snapshot.last_synced_at,
            observed_at,
        )
        if "columns" in changes:
            observation: ColumnObservation
            for observation in snapshot.columns:
                self._apply_column_observation(row.id, observation)
        row.updated_at = observed_at
        self._repo.commit()
        return table_read(row)

    def create_column(self, table_id: UUID, body: ColumnCreate) -> ColumnRead:
        """Add a column to a table."""
        self._table(table_id)
        self._ensure_column_name(table_id, body.name, None)
        now: datetime = utc_now()
        row: ColumnRow = ColumnRow(
            id=uuid4(),
            table_id=table_id,
            name=body.name,
            description=body.description,
            data_type=body.data_type,
            native_data_type=body.native_data_type,
            discovered_data_type=body.discovered_data_type,
            metadata_origin=body.metadata_origin.value,
            source_description=body.source_description,
            nullable=body.nullable,
            ordinal_position=body.ordinal_position,
            is_primary_key=body.is_primary_key,
            is_unique=body.is_unique,
            created_at=now,
            updated_at=now,
            last_seen_at=None,
        )
        stored: ColumnRow = self._repo.add_column(row)
        self._repo.commit()
        return column_read(stored)

    def get_column(self, column_id: UUID) -> ColumnRead:
        """Return one column."""
        return column_read(self._column(column_id))

    def list_columns(self, table_id: UUID) -> list[ColumnRead]:
        """List columns on a table."""
        self._table(table_id)
        return [column_read(row) for row in self._repo.list_columns(table_id)]

    def update_column(self, column_id: UUID, patch: ColumnUpdate) -> ColumnRead:
        """Patch user column fields. Null constraints mean unknown."""
        row: ColumnRow = self._column(column_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            _apply_user_context(row, patch.context)
        if "data_type" in changes:
            row.data_type = patch.data_type
        if "nullable" in changes:
            row.nullable = patch.nullable
        if "is_primary_key" in changes:
            row.is_primary_key = patch.is_primary_key
        if "is_unique" in changes:
            row.is_unique = patch.is_unique
        row.updated_at = utc_now()
        self._repo.commit()
        return column_read(row)

    def delete_column(self, column_id: UUID) -> None:
        """Delete a column that is not used by a relationship mapping."""
        row: ColumnRow = self._column(column_id)
        if self._repo.count_mappings_for_column(column_id) > 0:
            raise ConflictError("column is used by a relationship")
        self._repo.delete(row)
        self._repo.commit()

    def create_relationship(self, body: RelationshipCreate) -> RelationshipRead:
        """Create a manual or imported relationship with composite mappings."""
        self._table(body.source_table_id)
        self._table(body.target_table_id)
        positions: list[int] = _positions(body.column_mappings)
        self._validate_mappings(body.source_table_id, body.target_table_id, body.column_mappings)
        now: datetime = utc_now()
        row: RelationshipRow = RelationshipRow(
            id=uuid4(),
            source_table_id=body.source_table_id,
            target_table_id=body.target_table_id,
            relationship_type=body.relationship_type.value,
            origin=body.origin.value,
            status=body.status.value,
            description=body.description,
            created_at=now,
            updated_at=now,
        )
        stored: RelationshipRow = self._repo.add_relationship(row)
        self._insert_mappings(stored.id, body.column_mappings, positions)
        self._repo.commit()
        return self._relationship_read(stored)

    def get_relationship(self, relationship_id: UUID) -> RelationshipRead:
        """Return one relationship."""
        return self._relationship_read(self._relationship(relationship_id))

    def update_relationship(
        self,
        relationship_id: UUID,
        patch: RelationshipUpdate,
    ) -> RelationshipRead:
        """Patch a relationship. A mapping list replaces the previous pairs."""
        row: RelationshipRow = self._relationship(relationship_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "relationship_type" in changes:
            row.relationship_type = _required(patch.relationship_type, "relationship_type").value
        if "origin" in changes:
            row.origin = _required(patch.origin, "origin").value
        if "status" in changes:
            row.status = _required(patch.status, "status").value
        if "description" in changes:
            row.description = patch.description
        if "column_mappings" in changes:
            mappings: list[ColumnMappingWrite] = _required(patch.column_mappings, "column_mappings")
            if len(mappings) == 0:
                raise ValidationAppError(
                    "column_mappings must contain at least one pair",
                    field="column_mappings",
                )
            positions: list[int] = _positions(mappings)
            self._validate_mappings(row.source_table_id, row.target_table_id, mappings)
            self._repo.delete_mappings(row.id)
            self._insert_mappings(row.id, mappings, positions)
        row.updated_at = utc_now()
        self._repo.commit()
        return self._relationship_read(row)

    def delete_relationship(self, relationship_id: UUID) -> None:
        """Delete a relationship and its column mappings."""
        row: RelationshipRow = self._relationship(relationship_id)
        self._repo.delete_mappings(relationship_id)
        self._repo.delete(row)
        self._repo.commit()

    def list_table_relationships(self, table_id: UUID) -> TableRelationships:
        """Return incoming and outgoing relationships for a table."""
        self._table(table_id)
        incoming: list[RelationshipRead] = []
        outgoing: list[RelationshipRead] = []
        row: RelationshipRow
        for row in self._repo.list_relationships_for_table(table_id):
            read: RelationshipRead = self._relationship_read(row)
            if row.source_table_id == table_id:
                outgoing.append(read)
            if row.target_table_id == table_id and row.source_table_id != table_id:
                incoming.append(read)
        return TableRelationships(incoming=incoming, outgoing=outgoing)

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
            row.name = _required(patch.name, "name")
        if "endpoint" in changes:
            row.endpoint = _required(patch.endpoint, "endpoint")
        if "credential_ref" in changes:
            row.credential_ref = patch.credential_ref
        if "configuration" in changes:
            if patch.configuration is None:
                raise ValidationAppError("configuration cannot be null", field="configuration")
            row.configuration = dump_json_dict(patch.configuration)
        if "status" in changes:
            row.status = _required(patch.status, "status").value
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

    def create_source_binding(self, body: SourceBindingCreate) -> SourceBindingRead:
        """Bind a data source to an engine. Engine type is copied from the connection."""
        self._data_source(body.data_source_id)
        engine: EngineConnectionRow = self._engine(body.engine_connection_id)
        if body.default_scope_id is not None:
            self._scope(body.default_scope_id)
        self._ensure_binding_key(body.engine_connection_id, body.binding_key, None)
        now: datetime = utc_now()
        row: SourceEngineBindingRow = SourceEngineBindingRow(
            id=uuid4(),
            data_source_id=body.data_source_id,
            engine_connection_id=body.engine_connection_id,
            binding_key=body.binding_key,
            engine_type=engine.engine_type,
            configuration=dump_model(body.configuration),
            default_scope_id=body.default_scope_id,
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
            binding_key: str = _required(patch.binding_key, "binding_key")
            self._ensure_binding_key(row.engine_connection_id, binding_key, row.id)
            row.binding_key = binding_key
        if "configuration" in changes:
            if patch.configuration is None:
                raise ValidationAppError("configuration cannot be null", field="configuration")
            row.configuration = dump_model(patch.configuration)
        if "default_scope_id" in changes:
            if patch.default_scope_id is not None:
                self._scope(patch.default_scope_id)
            row.default_scope_id = patch.default_scope_id
        if "capabilities" in changes:
            if patch.capabilities is None:
                raise ValidationAppError("capabilities cannot be null", field="capabilities")
            row.capabilities = dump_model(patch.capabilities)
        row.updated_at = utc_now()
        self._repo.commit()
        return source_binding_read(row)

    def delete_source_binding(self, source_binding_id: UUID) -> None:
        """Delete a source binding that has no asset bindings or sync runs."""
        row: SourceEngineBindingRow = self._source_binding(source_binding_id)
        if self._repo.count_asset_bindings_for_source(source_binding_id) > 0:
            raise ConflictError("source binding still has asset bindings")
        if self._repo.count_sync_runs(source_binding_id) > 0:
            raise ConflictError("source binding still has sync runs")
        self._repo.delete(row)
        self._repo.commit()

    def create_asset_binding(
        self,
        table_id: UUID,
        body: AssetBindingCreate,
    ) -> AssetBindingRead:
        """Store an explicit engine address for a table.

        ``engine_ref_key`` is taken from the request. It is not copied from the table
        source key or from a binding on the other engine.
        """
        table: CatalogTableRow = self._table(table_id)
        source_binding: SourceEngineBindingRow = self._source_binding(body.source_engine_binding_id)
        if table.data_source_id != source_binding.data_source_id:
            raise ValidationAppError(
                "asset binding and source binding must use the same data source",
                field="source_engine_binding_id",
            )
        _assert_role_engine(body.role, source_binding.engine_type)
        _assert_selection(body.role, body.is_selected)
        self._ensure_engine_ref_key(source_binding.id, body.engine_ref_key, None)
        if body.is_selected:
            self._ensure_single_selected_query(table_id, None)
        now: datetime = utc_now()
        row: AssetEngineBindingRow = AssetEngineBindingRow(
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
        stored: AssetEngineBindingRow = self._repo.add_asset_binding(row)
        self._repo.commit()
        return asset_binding_read(stored)

    def list_asset_bindings(self, table_id: UUID) -> list[AssetBindingRead]:
        """List engine addresses stored for a table."""
        self._table(table_id)
        return [asset_binding_read(row) for row in self._repo.list_asset_bindings(table_id)]

    def get_asset_binding(self, binding_id: UUID) -> AssetBindingRead:
        """Return one asset binding."""
        return asset_binding_read(self._asset_binding(binding_id))

    def update_asset_binding(
        self,
        binding_id: UUID,
        patch: AssetBindingUpdate,
    ) -> AssetBindingRead:
        """Patch an asset address. Ref and key must change together."""
        row: AssetEngineBindingRow = self._asset_binding(binding_id)
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
            engine_ref_key: str = _required(patch.engine_ref_key, "engine_ref_key")
            self._ensure_engine_ref_key(row.source_engine_binding_id, engine_ref_key, row.id)
            row.engine_ref = dump_model(patch.engine_ref)
            row.engine_ref_key = engine_ref_key
        if "state" in changes:
            row.state = _required(patch.state, "state").value
        if "is_selected" in changes:
            is_selected: bool = _required(patch.is_selected, "is_selected")
            role: BindingRole = BindingRole(row.role)
            _assert_selection(role, is_selected)
            if is_selected:
                self._ensure_single_selected_query(row.catalog_table_id, row.id)
            row.is_selected = is_selected
        if "last_validated_at" in changes:
            row.last_validated_at = patch.last_validated_at
        row.updated_at = utc_now()
        self._repo.commit()
        return asset_binding_read(row)

    def delete_asset_binding(self, binding_id: UUID) -> None:
        """Delete one asset binding."""
        row: AssetEngineBindingRow = self._asset_binding(binding_id)
        self._repo.delete(row)
        self._repo.commit()

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
            new_status = _required(patch.status, "status")
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

    def _apply_snapshot_status(self, row: CatalogTableRow, status: TableStatus) -> None:
        if row.status == TableStatus.DEPRECATED.value:
            return
        if status is TableStatus.DEPRECATED:
            raise ValidationAppError(
                "imported snapshots cannot mark a table DEPRECATED",
                field="status",
            )
        row.status = status.value

    def _apply_column_observation(self, table_id: UUID, observation: ColumnObservation) -> None:
        column: ColumnRow | None = self._repo.find_column_by_name(table_id, observation.name)
        if column is None:
            raise ValidationAppError(
                f"column is not on the table: {observation.name}",
                field="columns",
            )
        changes: dict[str, object] = observation.model_dump(exclude_unset=True)
        if "discovered_data_type" in changes:
            column.discovered_data_type = observation.discovered_data_type
        if "native_data_type" in changes:
            column.native_data_type = observation.native_data_type
        if "source_description" in changes:
            column.source_description = observation.source_description
        if "nullable" in changes:
            column.nullable = observation.nullable
        if "ordinal_position" in changes and observation.ordinal_position is not None:
            column.ordinal_position = observation.ordinal_position
        if "is_primary_key" in changes:
            column.is_primary_key = observation.is_primary_key
        if "is_unique" in changes:
            column.is_unique = observation.is_unique
        if "metadata_origin" in changes and observation.metadata_origin is not None:
            column.metadata_origin = observation.metadata_origin.value
        column.updated_at = utc_now()

    def _validate_mappings(
        self,
        source_table_id: UUID,
        target_table_id: UUID,
        mappings: list[ColumnMappingWrite],
    ) -> None:
        seen: set[tuple[UUID, UUID]] = set()
        item: ColumnMappingWrite
        for item in mappings:
            pair: tuple[UUID, UUID] = (item.source_column_id, item.target_column_id)
            if pair in seen:
                raise ValidationAppError(
                    "column mappings must be unique",
                    field="column_mappings",
                )
            seen.add(pair)
            source_column: ColumnRow = self._column(item.source_column_id)
            target_column: ColumnRow = self._column(item.target_column_id)
            if source_column.table_id != source_table_id:
                raise ValidationAppError(
                    "source column does not belong to the source table",
                    field="column_mappings",
                )
            if target_column.table_id != target_table_id:
                raise ValidationAppError(
                    "target column does not belong to the target table",
                    field="column_mappings",
                )

    def _insert_mappings(
        self,
        relationship_id: UUID,
        mappings: list[ColumnMappingWrite],
        positions: list[int],
    ) -> None:
        index: int
        item: ColumnMappingWrite
        for index, item in enumerate(mappings):
            mapping: RelationshipColumnMappingRow = RelationshipColumnMappingRow(
                id=uuid4(),
                relationship_id=relationship_id,
                source_column_id=item.source_column_id,
                target_column_id=item.target_column_id,
                position=positions[index],
            )
            self._repo.add_mapping(mapping)

    def _relationship_read(self, row: RelationshipRow) -> RelationshipRead:
        mappings: list[RelationshipColumnMappingRow] = self._repo.list_mappings(row.id)
        names: dict[UUID, str] = {}
        mapping: RelationshipColumnMappingRow
        for mapping in mappings:
            source_column: ColumnRow = self._column(mapping.source_column_id)
            target_column: ColumnRow = self._column(mapping.target_column_id)
            names[source_column.id] = source_column.name
            names[target_column.id] = target_column.name
        return relationship_read(row, mappings, names)

    def _ensure_space_slug(self, slug: str, current_id: UUID | None) -> None:
        existing: SpaceRow | None = self._repo.find_space_by_slug(slug)
        if existing is not None and existing.id != current_id:
            raise ConflictError(f"space slug already exists: {slug}")

    def _ensure_scope_slug(self, space_id: UUID, slug: str, current_id: UUID | None) -> None:
        existing: ScopeRow | None = self._repo.find_scope_by_slug(space_id, slug)
        if existing is not None and existing.id != current_id:
            raise ConflictError(f"scope slug already exists: {slug}")

    def _ensure_source_key(
        self,
        data_source_id: UUID,
        source_key: str,
        current_id: UUID | None,
    ) -> None:
        existing: CatalogTableRow | None = self._repo.find_table_by_source_key(
            data_source_id,
            source_key,
        )
        if existing is not None and existing.id != current_id:
            raise ConflictError("source_key already exists for this data source")

    def _ensure_native_id(
        self,
        data_source_id: UUID,
        source_native_id: str,
        current_id: UUID | None,
    ) -> None:
        existing: CatalogTableRow | None = self._repo.find_table_by_native_id(
            data_source_id,
            source_native_id,
        )
        if existing is not None and existing.id != current_id:
            raise ConflictError("source_native_id already exists for this data source")

    def _ensure_column_name(self, table_id: UUID, name: str, current_id: UUID | None) -> None:
        existing: ColumnRow | None = self._repo.find_column_by_name(table_id, name)
        if existing is not None and existing.id != current_id:
            raise ConflictError(f"column name already exists on this table: {name}")

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
        existing: AssetEngineBindingRow | None = self._repo.find_asset_binding_by_key(
            source_binding_id,
            engine_ref_key,
        )
        if existing is not None and existing.id != current_id:
            raise ConflictError("engine_ref_key already exists for this source binding")

    def _ensure_single_selected_query(self, table_id: UUID, current_id: UUID | None) -> None:
        existing: AssetEngineBindingRow | None = self._repo.find_selected_query(table_id)
        if existing is not None and existing.id != current_id:
            raise ConflictError("a selected QUERY binding already exists for this table")

    def _space(self, space_id: UUID) -> SpaceRow:
        row: SpaceRow | None = self._repo.get_space(space_id)
        if row is None:
            raise EntityNotFoundError("space", str(space_id))
        return row

    def _scope(self, scope_id: UUID) -> ScopeRow:
        row: ScopeRow | None = self._repo.get_scope(scope_id)
        if row is None:
            raise EntityNotFoundError("scope", str(scope_id))
        return row

    def _data_source(self, data_source_id: UUID) -> DataSourceRow:
        row: DataSourceRow | None = self._repo.get_data_source(data_source_id)
        if row is None:
            raise EntityNotFoundError("data source", str(data_source_id))
        return row

    def _table(self, table_id: UUID) -> CatalogTableRow:
        row: CatalogTableRow | None = self._repo.get_table(table_id)
        if row is None:
            raise EntityNotFoundError("table", str(table_id))
        return row

    def _column(self, column_id: UUID) -> ColumnRow:
        row: ColumnRow | None = self._repo.get_column(column_id)
        if row is None:
            raise EntityNotFoundError("column", str(column_id))
        return row

    def _relationship(self, relationship_id: UUID) -> RelationshipRow:
        row: RelationshipRow | None = self._repo.get_relationship(relationship_id)
        if row is None:
            raise EntityNotFoundError("relationship", str(relationship_id))
        return row

    def _engine(self, engine_connection_id: UUID) -> EngineConnectionRow:
        row: EngineConnectionRow | None = self._repo.get_engine_connection(engine_connection_id)
        if row is None:
            raise EntityNotFoundError("engine connection", str(engine_connection_id))
        return row

    def _source_binding(self, source_binding_id: UUID) -> SourceEngineBindingRow:
        row: SourceEngineBindingRow | None = self._repo.get_source_binding(source_binding_id)
        if row is None:
            raise EntityNotFoundError("source binding", str(source_binding_id))
        return row

    def _asset_binding(self, binding_id: UUID) -> AssetEngineBindingRow:
        row: AssetEngineBindingRow | None = self._repo.get_asset_binding(binding_id)
        if row is None:
            raise EntityNotFoundError("asset binding", str(binding_id))
        return row

    def _sync_run(self, sync_run_id: UUID) -> MetadataSyncRunRow:
        row: MetadataSyncRunRow | None = self._repo.get_sync_run(sync_run_id)
        if row is None:
            raise EntityNotFoundError("sync run", str(sync_run_id))
        return row


def _apply_user_context(
    row: SpaceRow | ScopeRow | CatalogTableRow | ColumnRow,
    value: str | None,
) -> None:
    """Store a manual context edit. Blank text clears both the text and the origin."""
    text: str | None = None if value is None else value.strip()
    if text == "":
        text = None
    row.context = text
    row.context_origin = None if text is None else ContextOrigin.USER.value


def _slug_for(name: str, explicit: str | None) -> str:
    try:
        if explicit is not None:
            return explicit
        return slugify(name)
    except ValueError as err:
        raise ValidationAppError(str(err), field="slug") from err


def _required[T](value: T | None, field: str) -> T:
    if value is None:
        raise ValidationAppError(f"{field} cannot be null", field=field)
    return value


def _positions(mappings: list[ColumnMappingWrite]) -> list[int]:
    omitted: bool = any(item.position is None for item in mappings)
    provided: bool = any(item.position is not None for item in mappings)
    if omitted and provided:
        raise ValidationAppError(
            "column mapping positions must all be set or all omitted",
            field="column_mappings",
        )
    if omitted:
        return list(range(len(mappings)))
    positions: list[int] = []
    item: ColumnMappingWrite
    for item in mappings:
        if item.position is None:
            raise ValidationAppError(
                "column mapping positions must all be set or all omitted",
                field="column_mappings",
            )
        positions.append(item.position)
    if len(set(positions)) != len(positions):
        raise ValidationAppError(
            "column mapping positions must be unique",
            field="column_mappings",
        )
    return positions


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


def _observed_time(
    changes: dict[str, object],
    field: str,
    explicit: datetime | None,
    fallback: datetime,
) -> datetime:
    if field in changes and explicit is not None:
        return explicit
    return fallback


def _sync_finished_at(status: SyncRunStatus, finished_at: datetime | None) -> datetime | None:
    if status is SyncRunStatus.RUNNING and finished_at is not None:
        raise ValidationAppError("a running sync has no finished_at", field="finished_at")
    if status in _TERMINAL_SYNC and finished_at is None:
        return utc_now()
    return finished_at
