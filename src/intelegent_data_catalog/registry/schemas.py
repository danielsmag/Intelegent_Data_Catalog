"""HTTP and service models for the application catalog."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from intelegent_data_catalog.persistence.enums import (
    BindingRole,
    BindingState,
    CapabilitySupport,
    ContextOrigin,
    DataSourceType,
    EngineConnectionStatus,
    EngineType,
    MetadataOrigin,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    SyncRunStatus,
    TableKind,
    TableStatus,
    WorkspaceNodeKind,
    WorkspaceRole,
)
from intelegent_data_catalog.registry.identifiers import exact_identifier, require_slug
from intelegent_data_catalog.registry.secrets import assert_no_secrets, reject_endpoint_credentials


def _optional_credential_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip() == "":
        raise ValueError("credential_ref cannot be blank")
    return value


def _optional_display_name(value: str | None) -> str | None:
    """Trim a patched name. Blank text is rejected; an omitted name stays omitted."""
    if value is None:
        return None
    name: str = value.strip()
    if name == "":
        raise ValueError("name cannot be blank")
    return name


def _secret_free_configuration(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    assert_no_secrets(value, "configuration")
    return value


class SourceRef(BaseModel):
    """Structured source-native address. Case is significant."""

    model_config = ConfigDict(extra="forbid")

    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    table: str = Field(min_length=1, max_length=512)

    @field_validator("catalog", "namespace", "table")
    @classmethod
    def _exact(cls, value: str) -> str:
        return exact_identifier(value)


class EngineRef(BaseModel):
    """Structured address in one engine. Not computed from another engine's address."""

    model_config = ConfigDict(extra="forbid")

    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    table: str = Field(min_length=1, max_length=512)
    session_setup: list[str] = Field(default_factory=list)

    @field_validator("catalog", "namespace", "table")
    @classmethod
    def _exact(cls, value: str) -> str:
        return exact_identifier(value)

    @model_validator(mode="after")
    def _session_setup_has_no_secrets(self) -> EngineRef:
        assert_no_secrets(self.session_setup, "engine_ref.session_setup")
        return self


class SourceBindingConfig(BaseModel):
    """Non-secret catalog and schema allowlists for one source binding."""

    model_config = ConfigDict(extra="forbid")

    catalog_allowlist: list[str] = Field(default_factory=list)
    schema_allowlist: list[str] = Field(default_factory=list)
    mappings: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_secrets(self) -> SourceBindingConfig:
        assert_no_secrets(self.model_dump(), "configuration")
        return self


class SourceCapabilities(BaseModel):
    """Claimed metadata features. Defaults are UNKNOWN, not unsupported."""

    model_config = ConfigDict(extra="forbid")

    columns: CapabilitySupport = CapabilitySupport.UNKNOWN
    relationships: CapabilitySupport = CapabilitySupport.UNKNOWN
    statistics: CapabilitySupport = CapabilitySupport.UNKNOWN
    constraints: CapabilitySupport = CapabilitySupport.UNKNOWN


class ScanScope(BaseModel):
    """Catalogs and schemas a sync run said it would visit."""

    model_config = ConfigDict(extra="forbid")

    catalogs: list[str] = Field(default_factory=list)
    schemas: list[str] = Field(default_factory=list)


class SyncCounts(BaseModel):
    """Counters stored on a sync run. They are not recomputed here."""

    model_config = ConfigDict(extra="forbid")

    tables_seen: int = Field(default=0, ge=0)
    columns_seen: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)








class DataSourceCreate(BaseModel):
    """Register a source. Configuration must not contain secrets."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    type: DataSourceType
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    credential_ref: str | None = Field(default=None, max_length=1024)

    @field_validator("configuration")
    @classmethod
    def _configuration(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _secret_free_configuration(value)

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str | None) -> str | None:
        return _optional_credential_ref(value)


class DataSourceUpdate(BaseModel):
    """Patch a source. Source type stays fixed."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    configuration: dict[str, JsonValue] | None = None
    credential_ref: str | None = Field(default=None, max_length=1024)

    @field_validator("configuration")
    @classmethod
    def _configuration(
        cls,
        value: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None:
        if value is None:
            return None
        return _secret_free_configuration(value)

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str | None) -> str | None:
        return _optional_credential_ref(value)


class DataSourceRead(BaseModel):
    """Source returned by the API. Only the secret reference is exposed."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    type: DataSourceType
    configuration: dict[str, JsonValue]
    credential_ref: str | None
    created_at: datetime
    updated_at: datetime


class CatalogTableCreate(BaseModel):
    """Create a catalog table."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: UUID
    name: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=16000)
    table_type: TableKind
    status: TableStatus = TableStatus.ACTIVE
    physical_catalog: str = Field(min_length=1, max_length=512)
    physical_namespace: str = Field(min_length=1, max_length=512)
    physical_table: str = Field(min_length=1, max_length=512)
    source_ref: SourceRef
    source_key: str | None = Field(default=None, max_length=2048)
    source_native_id: str | None = Field(default=None, max_length=1024)
    table_format: str | None = Field(default=None, max_length=64)
    source_description: str | None = Field(default=None, max_length=16000)
    row_count: int | None = Field(default=None, ge=0)

    @field_validator(
        "name",
        "physical_catalog",
        "physical_namespace",
        "physical_table",
        "source_key",
        "source_native_id",
        "table_format",
    )
    @classmethod
    def _exact(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return exact_identifier(value)

    @model_validator(mode="after")
    def _physical_matches_source_ref(self) -> CatalogTableCreate:
        if (
            self.physical_catalog != self.source_ref.catalog
            or self.physical_namespace != self.source_ref.namespace
            or self.physical_table != self.source_ref.table
        ):
            raise ValueError(
                "physical_catalog, physical_namespace, and physical_table must match source_ref"
            )
        return self


class CatalogTableUpdate(BaseModel):
    """User edits. Physical identity and imported description stay on other paths."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=16000)
    context: str | None = Field(default=None, max_length=4000)
    status: TableStatus | None = None
    table_format: str | None = Field(default=None, max_length=64)

    @field_validator("name", "table_format")
    @classmethod
    def _exact(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return exact_identifier(value)


class CatalogTableRead(BaseModel):
    """Catalog table returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    agent_id: UUID | None = None
    data_source_id: UUID
    name: str
    display_name: str | None
    description: str | None
    context: str | None
    context_origin: ContextOrigin | None
    table_type: TableKind
    status: TableStatus
    physical_catalog: str
    physical_namespace: str
    physical_table: str
    source_ref: SourceRef
    source_key: str
    source_native_id: str | None
    table_format: str | None
    source_description: str | None
    row_count: int | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None
    last_synced_at: datetime | None


class ColumnCreate(BaseModel):
    """Add a column. User description and imported description are separate fields."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=16000)
    data_type: str | None = Field(default=None, max_length=512)
    native_data_type: str | None = Field(default=None, max_length=512)
    discovered_data_type: str | None = Field(default=None, max_length=512)
    metadata_origin: MetadataOrigin = MetadataOrigin.USER
    source_description: str | None = Field(default=None, max_length=16000)
    nullable: bool | None = None
    ordinal_position: int = Field(ge=0)
    is_primary_key: bool | None = None
    is_unique: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return exact_identifier(value)


class ColumnUpdate(BaseModel):
    """User column edits. Discovered and native types are not patched here."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=16000)
    context: str | None = Field(default=None, max_length=4000)
    data_type: str | None = Field(default=None, max_length=512)
    nullable: bool | None = None
    is_primary_key: bool | None = None
    is_unique: bool | None = None


class ColumnRead(BaseModel):
    """Column returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    table_id: UUID
    name: str
    description: str | None
    context: str | None
    context_origin: ContextOrigin | None
    data_type: str | None
    native_data_type: str | None
    discovered_data_type: str | None
    metadata_origin: MetadataOrigin
    source_description: str | None
    nullable: bool | None
    ordinal_position: int
    is_primary_key: bool | None
    is_unique: bool | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None


class ColumnObservation(BaseModel):
    """Imported column fields. User description is intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    discovered_data_type: str | None = Field(default=None, max_length=512)
    native_data_type: str | None = Field(default=None, max_length=512)
    source_description: str | None = Field(default=None, max_length=16000)
    nullable: bool | None = None
    ordinal_position: int | None = Field(default=None, ge=0)
    is_primary_key: bool | None = None
    is_unique: bool | None = None
    metadata_origin: MetadataOrigin | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return exact_identifier(value)


class SourceSnapshot(BaseModel):
    """Imported table fields applied without touching user description or scope."""

    model_config = ConfigDict(extra="forbid")

    source_description: str | None = Field(default=None, max_length=16000)
    row_count: int | None = Field(default=None, ge=0)
    status: TableStatus | None = None
    last_seen_at: datetime | None = None
    last_synced_at: datetime | None = None
    columns: list[ColumnObservation] = Field(default_factory=list)


class ColumnMappingWrite(BaseModel):
    """One pair in a composite relationship."""

    model_config = ConfigDict(extra="forbid")

    source_column_id: UUID
    target_column_id: UUID
    position: int | None = Field(default=None, ge=0)


class ColumnMappingRead(BaseModel):
    """Stored column pair, with names for display."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_column_id: UUID
    target_column_id: UUID
    source_column_name: str
    target_column_name: str
    position: int


class RelationshipCreate(BaseModel):
    """Create a relationship and its column mappings."""

    model_config = ConfigDict(extra="forbid")

    source_table_id: UUID
    target_table_id: UUID
    relationship_type: RelationshipType = RelationshipType.MANUAL
    origin: RelationshipOrigin = RelationshipOrigin.MANUAL
    status: RelationshipStatus = RelationshipStatus.UNVERIFIED
    description: str | None = Field(default=None, max_length=16000)
    column_mappings: list[ColumnMappingWrite] = Field(min_length=1)


class RelationshipUpdate(BaseModel):
    """Patch a relationship. Sending column_mappings replaces the whole set."""

    model_config = ConfigDict(extra="forbid")

    relationship_type: RelationshipType | None = None
    origin: RelationshipOrigin | None = None
    status: RelationshipStatus | None = None
    description: str | None = Field(default=None, max_length=16000)
    column_mappings: list[ColumnMappingWrite] | None = None


class RelationshipRead(BaseModel):
    """Relationship returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_table_id: UUID
    target_table_id: UUID
    relationship_type: RelationshipType
    origin: RelationshipOrigin
    status: RelationshipStatus
    description: str | None
    column_mappings: list[ColumnMappingRead]
    created_at: datetime
    updated_at: datetime


class TableRelationships(BaseModel):
    """Relationships touching one table, split by direction."""

    model_config = ConfigDict(extra="forbid")

    incoming: list[RelationshipRead]
    outgoing: list[RelationshipRead]


class EngineConnectionCreate(BaseModel):
    """Register an engine endpoint. The URL must not carry credentials."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    engine_type: EngineType
    endpoint: str = Field(min_length=1, max_length=2048)
    credential_ref: str | None = Field(default=None, max_length=1024)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    status: EngineConnectionStatus = EngineConnectionStatus.ACTIVE

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        return reject_endpoint_credentials(value)

    @field_validator("configuration")
    @classmethod
    def _configuration(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _secret_free_configuration(value)

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str | None) -> str | None:
        return _optional_credential_ref(value)


class EngineConnectionUpdate(BaseModel):
    """Patch an engine connection. Engine type stays fixed."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    credential_ref: str | None = Field(default=None, max_length=1024)
    configuration: dict[str, JsonValue] | None = None
    status: EngineConnectionStatus | None = None

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return reject_endpoint_credentials(value)

    @field_validator("configuration")
    @classmethod
    def _configuration(
        cls,
        value: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None:
        if value is None:
            return None
        return _secret_free_configuration(value)

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str | None) -> str | None:
        return _optional_credential_ref(value)


class EngineConnectionRead(BaseModel):
    """Engine connection returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    engine_type: EngineType
    endpoint: str
    credential_ref: str | None
    configuration: dict[str, JsonValue]
    status: EngineConnectionStatus
    created_at: datetime
    updated_at: datetime


class SourceBindingCreate(BaseModel):
    """Bind a data source to an engine connection."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: UUID
    engine_connection_id: UUID
    binding_key: str = Field(min_length=1, max_length=512)
    configuration: SourceBindingConfig = Field(default_factory=SourceBindingConfig)
    capabilities: SourceCapabilities = Field(default_factory=SourceCapabilities)

    @field_validator("binding_key")
    @classmethod
    def _binding_key(cls, value: str) -> str:
        return exact_identifier(value)


class SourceBindingUpdate(BaseModel):
    """Patch allowlists or capabilities."""

    model_config = ConfigDict(extra="forbid")

    binding_key: str | None = Field(default=None, max_length=512)
    configuration: SourceBindingConfig | None = None
    capabilities: SourceCapabilities | None = None

    @field_validator("binding_key")
    @classmethod
    def _binding_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return exact_identifier(value)


class SourceBindingRead(BaseModel):
    """Source binding returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    data_source_id: UUID
    engine_connection_id: UUID
    binding_key: str
    engine_type: EngineType
    configuration: SourceBindingConfig
    capabilities: SourceCapabilities
    created_at: datetime
    updated_at: datetime


class AgentBindingCreate(BaseModel):
    """Attach an explicit engine address to a catalog table."""

    model_config = ConfigDict(extra="forbid")

    source_engine_binding_id: UUID
    engine_ref: EngineRef
    engine_ref_key: str = Field(min_length=1, max_length=2048)
    role: BindingRole
    state: BindingState = BindingState.UNVALIDATED
    is_selected: bool = False
    last_validated_at: datetime | None = None

    @field_validator("engine_ref_key")
    @classmethod
    def _engine_ref_key(cls, value: str) -> str:
        return exact_identifier(value)


class AgentBindingUpdate(BaseModel):
    """Patch an agent binding. Role and source binding stay fixed."""

    model_config = ConfigDict(extra="forbid")

    engine_ref: EngineRef | None = None
    engine_ref_key: str | None = Field(default=None, max_length=2048)
    state: BindingState | None = None
    is_selected: bool | None = None
    last_validated_at: datetime | None = None

    @field_validator("engine_ref_key")
    @classmethod
    def _engine_ref_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return exact_identifier(value)


class AgentBindingRead(BaseModel):
    """Agent binding returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    catalog_table_id: UUID
    data_source_id: UUID
    source_engine_binding_id: UUID
    engine_type: EngineType
    engine_ref: EngineRef
    engine_ref_key: str
    role: BindingRole
    state: BindingState
    is_selected: bool
    last_validated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SyncRunCreate(BaseModel):
    """Record a sync run. This does not contact Trino."""

    model_config = ConfigDict(extra="forbid")

    source_engine_binding_id: UUID
    status: SyncRunStatus = SyncRunStatus.RUNNING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    scan_scope: ScanScope = Field(default_factory=ScanScope)
    counts: SyncCounts = Field(default_factory=SyncCounts)
    sanitized_error: str | None = Field(default=None, max_length=16000)
    checkpoint: dict[str, JsonValue] | None = None

    @field_validator("checkpoint")
    @classmethod
    def _checkpoint(
        cls,
        value: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None:
        if value is None:
            return None
        assert_no_secrets(value, "checkpoint")
        return value


class SyncRunUpdate(BaseModel):
    """Update a recorded sync run."""

    model_config = ConfigDict(extra="forbid")

    status: SyncRunStatus | None = None
    finished_at: datetime | None = None
    scan_scope: ScanScope | None = None
    counts: SyncCounts | None = None
    sanitized_error: str | None = Field(default=None, max_length=16000)
    checkpoint: dict[str, JsonValue] | None = None

    @field_validator("checkpoint")
    @classmethod
    def _checkpoint(
        cls,
        value: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None:
        if value is None:
            return None
        assert_no_secrets(value, "checkpoint")
        return value






class SyncRunRead(BaseModel):
    """Sync run returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_engine_binding_id: UUID
    started_at: datetime
    finished_at: datetime | None
    status: SyncRunStatus
    scan_scope: ScanScope
    counts: SyncCounts
    sanitized_error: str | None
    checkpoint: dict[str, JsonValue] | None


class MembershipRead(BaseModel):
    """One workspace the caller belongs to."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    name: str
    slug: str
    role: WorkspaceRole
    is_system: bool


class MeRead(BaseModel):
    """Caller identity and workspace memberships."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    email: str | None
    name: str
    user_id: UUID
    is_admin: bool
    workspace_id: UUID | None
    memberships: list[MembershipRead]


class WorkspaceCreate(BaseModel):
    """Create a workspace. Slug is derived from the name when omitted."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=16000)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_slug(value)


class WorkspaceUpdate(BaseModel):
    """Patch a workspace name or description."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=16000)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_slug(value)


class WorkspaceRead(BaseModel):
    """Workspace returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    slug: str
    description: str | None
    is_system: bool
    role: WorkspaceRole | None
    created_at: datetime
    updated_at: datetime


class WorkspaceMemberCreate(BaseModel):
    """Add a user who has already signed in, by email."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    role: WorkspaceRole = WorkspaceRole.MEMBER


class WorkspaceMemberRead(BaseModel):
    """Member of a workspace."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    email: str | None
    display_name: str
    role: WorkspaceRole
    created_at: datetime


class WorkspaceNodeRead(BaseModel):
    """Folder or file in a workspace tree."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    parent_id: UUID | None
    name: str
    path: str
    kind: WorkspaceNodeKind
    content_type: str | None
    size_bytes: int | None
    owner_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class FolderCreate(BaseModel):
    """Create a folder under a parent."""

    model_config = ConfigDict(extra="forbid")

    parent_id: UUID
    name: str = Field(min_length=1, max_length=255)


class TextFileCreate(BaseModel):
    """Create a text file under a folder."""

    model_config = ConfigDict(extra="forbid")

    parent_id: UUID
    name: str = Field(min_length=1, max_length=255)
    text: str = Field(max_length=5_000_000)


class NodeUpdate(BaseModel):
    """Rename a node or move it to another folder."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: UUID | None = None
