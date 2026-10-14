"""Canonical catalog tables.

``description`` columns are user overrides. ``source_description`` columns are imported
text. Spark query addresses live on asset bindings and are not derived from Trino keys.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from intelegent_data_catalog.persistence.enums import (
    AirflowInstanceStatus,
    AirflowMappingDirection,
    AirflowMappingOrigin,
    BindingRole,
    BindingState,
    CatalogJobKind,
    CatalogJobStatus,
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
    TagOrigin,
)


class Base(DeclarativeBase):
    """Shared metadata for the catalog schema."""


def _enum_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    listed: str = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({listed})", name=name)


class SpaceRow(Base):
    """Business space. Not a Trino catalog."""

    __tablename__: str = "spaces"
    __table_args__: tuple[CheckConstraint, ...] = (
        _enum_check(
            "context_origin",
            tuple(item.value for item in ContextOrigin),
            "ck_spaces_context_origin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScopeRow(Base):
    """Business scope inside one space."""

    __tablename__: str = "scopes"
    __table_args__: tuple[UniqueConstraint | CheckConstraint, ...] = (
        UniqueConstraint("space_id", "slug", name="uq_scopes_space_slug"),
        _enum_check(
            "context_origin",
            tuple(item.value for item in ContextOrigin),
            "ck_scopes_context_origin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    space_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("spaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DataSourceRow(Base):
    """Registered source. Creating a row does not provision a connector."""

    __tablename__: str = "data_sources"
    __table_args__: tuple[CheckConstraint] = (
        _enum_check("type", tuple(item.value for item in DataSourceType), "ck_data_sources_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CatalogTableRow(Base):
    """Catalog table identity is the UUID plus the source-qualified key."""

    __tablename__: str = "catalog_tables"
    __table_args__: tuple[UniqueConstraint | CheckConstraint | Index, ...] = (
        UniqueConstraint("data_source_id", "source_key", name="uq_catalog_tables_source_key"),
        UniqueConstraint("id", "data_source_id", name="uq_catalog_tables_id_source"),
        _enum_check(
            "table_type",
            tuple(item.value for item in TableKind),
            "ck_catalog_tables_table_type",
        ),
        _enum_check(
            "status",
            tuple(item.value for item in TableStatus),
            "ck_catalog_tables_status",
        ),
        CheckConstraint(
            "row_count IS NULL OR row_count >= 0",
            name="ck_catalog_tables_row_count",
        ),
        _enum_check(
            "context_origin",
            tuple(item.value for item in ContextOrigin),
            "ck_catalog_tables_context_origin",
        ),
        Index(
            "uq_catalog_tables_native_id",
            "data_source_id",
            "source_native_id",
            unique=True,
            sqlite_where=text("source_native_id IS NOT NULL"),
            postgresql_where=text("source_native_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scopes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    table_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    physical_catalog: Mapped[str] = mapped_column(String(512), nullable=False)
    physical_namespace: Mapped[str] = mapped_column(String(512), nullable=False)
    physical_table: Mapped[str] = mapped_column(String(512), nullable=False)
    source_ref: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_native_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    table_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ColumnRow(Base):
    """Column on a catalog table. Null constraints mean unknown, not false."""

    __tablename__: str = "columns"
    __table_args__: tuple[UniqueConstraint | CheckConstraint, ...] = (
        UniqueConstraint("table_id", "name", name="uq_columns_table_name"),
        _enum_check(
            "metadata_origin",
            tuple(item.value for item in MetadataOrigin),
            "ck_columns_metadata_origin",
        ),
        CheckConstraint("ordinal_position >= 0", name="ck_columns_ordinal"),
        _enum_check(
            "context_origin",
            tuple(item.value for item in ContextOrigin),
            "ck_columns_context_origin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    table_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_type: Mapped[str | None] = mapped_column(String(512), nullable=True)
    native_data_type: Mapped[str | None] = mapped_column(String(512), nullable=True)
    discovered_data_type: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_origin: Mapped[str] = mapped_column(String(32), nullable=False)
    source_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nullable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_primary_key: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_unique: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RelationshipRow(Base):
    """Semantic link between tables. Composite keys live in column mappings."""

    __tablename__: str = "relationships"
    __table_args__: tuple[CheckConstraint | Index, ...] = (
        _enum_check(
            "relationship_type",
            tuple(item.value for item in RelationshipType),
            "ck_relationships_type",
        ),
        _enum_check(
            "origin",
            tuple(item.value for item in RelationshipOrigin),
            "ck_relationships_origin",
        ),
        _enum_check(
            "status",
            tuple(item.value for item in RelationshipStatus),
            "ck_relationships_status",
        ),
        Index("ix_relationships_source_table", "source_table_id"),
        Index("ix_relationships_target_table", "target_table_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_table_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_table_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="RESTRICT"),
        nullable=False,
    )
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RelationshipColumnMappingRow(Base):
    """One column pair in a relationship. Position orders composite keys."""

    __tablename__: str = "relationship_column_mappings"
    __table_args__: tuple[UniqueConstraint | CheckConstraint, ...] = (
        UniqueConstraint("relationship_id", "position", name="uq_relationship_mappings_position"),
        UniqueConstraint(
            "relationship_id",
            "source_column_id",
            "target_column_id",
            name="uq_relationship_mappings_columns",
        ),
        CheckConstraint("position >= 0", name="ck_relationship_mappings_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    relationship_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("relationships.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_column_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("columns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_column_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("columns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class EngineConnectionRow(Base):
    """Named Trino or Spark SQL endpoint. Credentials stay in ``credential_ref``."""

    __tablename__: str = "engine_connections"
    __table_args__: tuple[UniqueConstraint | CheckConstraint, ...] = (
        UniqueConstraint("id", "engine_type", name="uq_engine_connections_id_type"),
        _enum_check(
            "engine_type",
            tuple(item.value for item in EngineType),
            "ck_engine_connections_type",
        ),
        _enum_check(
            "status",
            tuple(item.value for item in EngineConnectionStatus),
            "ck_engine_connections_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    engine_type: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceEngineBindingRow(Base):
    """How one engine sees one data source. ``binding_key`` is the engine catalog alias."""

    __tablename__: str = "source_engine_bindings"
    __table_args__: tuple[UniqueConstraint | ForeignKeyConstraint, ...] = (
        UniqueConstraint(
            "engine_connection_id",
            "binding_key",
            name="uq_source_bindings_connection_key",
        ),
        UniqueConstraint(
            "id",
            "data_source_id",
            "engine_type",
            name="uq_source_bindings_id_source_engine",
        ),
        ForeignKeyConstraint(
            ["engine_connection_id", "engine_type"],
            ["engine_connections.id", "engine_connections.engine_type"],
            name="fk_source_bindings_connection_type",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    engine_connection_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    binding_key: Mapped[str] = mapped_column(String(512), nullable=False)
    engine_type: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    default_scope_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scopes.id", ondelete="RESTRICT"),
        nullable=True,
    )
    capabilities: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssetEngineBindingRow(Base):
    """Per-asset engine address. Query keys are stored explicitly."""

    __tablename__: str = "asset_engine_bindings"
    __table_args__: tuple[
        UniqueConstraint | CheckConstraint | ForeignKeyConstraint | Index,
        ...,
    ] = (
        UniqueConstraint(
            "source_engine_binding_id",
            "engine_ref_key",
            name="uq_asset_bindings_ref_key",
        ),
        CheckConstraint(
            "(role = 'DISCOVERY' AND engine_type = 'TRINO') "
            "OR (role = 'QUERY' AND engine_type = 'SPARK_SQL')",
            name="ck_asset_bindings_role_engine",
        ),
        CheckConstraint(
            "(NOT is_selected) OR (role = 'QUERY')",
            name="ck_asset_bindings_selected_query",
        ),
        _enum_check("role", tuple(item.value for item in BindingRole), "ck_asset_bindings_role"),
        _enum_check(
            "state",
            tuple(item.value for item in BindingState),
            "ck_asset_bindings_state",
        ),
        ForeignKeyConstraint(
            ["catalog_table_id", "data_source_id"],
            ["catalog_tables.id", "catalog_tables.data_source_id"],
            name="fk_asset_bindings_table_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_engine_binding_id", "data_source_id", "engine_type"],
            [
                "source_engine_bindings.id",
                "source_engine_bindings.data_source_id",
                "source_engine_bindings.engine_type",
            ],
            name="fk_asset_bindings_source_engine",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_asset_bindings_one_selected_query",
            "catalog_table_id",
            unique=True,
            sqlite_where=text("role = 'QUERY' AND is_selected"),
            postgresql_where=text("role = 'QUERY' AND is_selected"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    catalog_table_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    data_source_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_engine_binding_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    engine_type: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_ref: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    engine_ref_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetadataSyncRunRow(Base):
    """Record of a discovery scan. This table does not run the scan."""

    __tablename__: str = "metadata_sync_runs"
    __table_args__: tuple[CheckConstraint] = (
        _enum_check(
            "status",
            tuple(item.value for item in SyncRunStatus),
            "ck_sync_runs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_engine_binding_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_engine_bindings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scan_scope: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    counts: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    sanitized_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)


class TagRow(Base):
    """Catalog tag. Tags are not part of the space hierarchy."""

    __tablename__: str = "tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TableTagRow(Base):
    """Tag on a table. USER attachments are kept when imports run."""

    __tablename__: str = "table_tags"
    __table_args__: tuple[UniqueConstraint | CheckConstraint, ...] = (
        UniqueConstraint("table_id", "tag_id", name="uq_table_tags_table_tag"),
        _enum_check("origin", tuple(item.value for item in TagOrigin), "ck_table_tags_origin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    table_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        nullable=False,
    )
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TableKnowledgeContextRow(Base):
    """Latest built analysis context for one catalog table."""

    __tablename__: str = "table_knowledge_contexts"
    __table_args__: tuple[UniqueConstraint, ...] = (
        UniqueConstraint("table_id", name="uq_table_knowledge_contexts_table"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    table_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SpaceKnowledgeContextRow(Base):
    """Latest built analysis context for one catalog space."""

    __tablename__: str = "space_knowledge_contexts"
    __table_args__: tuple[UniqueConstraint, ...] = (
        UniqueConstraint("space_id", name="uq_space_knowledge_contexts_space"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    space_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("spaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScopeKnowledgeContextRow(Base):
    """Latest built analysis context for one catalog scope."""

    __tablename__: str = "scope_knowledge_contexts"
    __table_args__: tuple[UniqueConstraint, ...] = (
        UniqueConstraint("scope_id", name="uq_scope_knowledge_contexts_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scopes.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarquezMappingRow(Base):
    """Catalog table identity mapped to a Marquez dataset. Marquez ids are not catalog ids."""

    __tablename__: str = "marquez_mappings"
    __table_args__: tuple[UniqueConstraint, ...] = (
        UniqueConstraint(
            "marquez_namespace",
            "marquez_dataset_name",
            name="uq_marquez_mappings_dataset",
        ),
        UniqueConstraint("catalog_table_id", name="uq_marquez_mappings_table"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    catalog_table_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    marquez_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    marquez_dataset_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AirflowInstanceRow(Base):
    """Registered Airflow 3 deployment. The password is stored and never returned."""

    __tablename__: str = "airflow_instances"
    __table_args__: tuple[UniqueConstraint | CheckConstraint, ...] = (
        UniqueConstraint("base_url", name="uq_airflow_instances_base_url"),
        _enum_check(
            "status",
            tuple(item.value for item in AirflowInstanceStatus),
            "ck_airflow_instances_status",
        ),
        _enum_check(
            "context_origin",
            tuple(item.value for item in ContextOrigin),
            "ck_airflow_instances_context_origin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(Text, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        """Omit the password. SQLAlchemy's default repr would include it."""
        return (
            f"AirflowInstanceRow(id={self.id!r}, name={self.name!r}, "
            f"slug={self.slug!r}, base_url={self.base_url!r})"
        )


class AirflowDagRow(Base):
    """Synced DAG snapshot. Live runs are read from Airflow and not stored here."""

    __tablename__: str = "airflow_dags"
    __table_args__: tuple[UniqueConstraint, ...] = (
        UniqueConstraint("instance_id", "dag_id", name="uq_airflow_dags_instance_dag"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airflow_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    dag_id: Mapped[str] = mapped_column(String(250), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_import_errors: Mapped[bool] = mapped_column(Boolean, nullable=False)
    timetable_summary: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    timetable_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owners: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    fileloc: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    bundle_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_active_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_dagrun_run_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_parsed_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    tasks: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    last_run: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    run_stats: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    source_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AirflowDagTableRow(Base):
    """DAG to catalog table link. Sync replaces ASSET and SOURCE_MATCH, not MANUAL or LLM."""

    __tablename__: str = "airflow_dag_tables"
    __table_args__: tuple[UniqueConstraint | CheckConstraint | Index, ...] = (
        UniqueConstraint(
            "dag_row_id",
            "catalog_table_id",
            "origin",
            name="uq_airflow_dag_tables_link",
        ),
        _enum_check(
            "origin",
            tuple(item.value for item in AirflowMappingOrigin),
            "ck_airflow_dag_tables_origin",
        ),
        _enum_check(
            "direction",
            tuple(item.value for item in AirflowMappingDirection),
            "ck_airflow_dag_tables_direction",
        ),
        Index("ix_airflow_dag_tables_table", "catalog_table_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    dag_row_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airflow_dags.id", ondelete="CASCADE"),
        nullable=False,
    )
    catalog_table_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)


_ONE_AIRFLOW_SOURCE: str = (
    "(airflow_instance_id IS NOT NULL AND dag_row_id IS NULL) OR "
    "(airflow_instance_id IS NULL AND dag_row_id IS NOT NULL)"
)
_ONE_AIRFLOW_TARGET: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL) OR (space_id IS NULL AND scope_id IS NOT NULL)"
)


class AirflowAttachmentRow(Base):
    """Airflow instance or one DAG attached to a space or a scope. Many targets allowed."""

    __tablename__: str = "airflow_attachments"
    __table_args__: tuple[CheckConstraint | Index, ...] = (
        CheckConstraint(_ONE_AIRFLOW_SOURCE, name="ck_airflow_attachments_one_source"),
        CheckConstraint(_ONE_AIRFLOW_TARGET, name="ck_airflow_attachments_one_target"),
        Index(
            "uq_airflow_attachments_instance_space",
            "airflow_instance_id",
            "space_id",
            unique=True,
            sqlite_where=text("airflow_instance_id IS NOT NULL AND space_id IS NOT NULL"),
            postgresql_where=text("airflow_instance_id IS NOT NULL AND space_id IS NOT NULL"),
        ),
        Index(
            "uq_airflow_attachments_instance_scope",
            "airflow_instance_id",
            "scope_id",
            unique=True,
            sqlite_where=text("airflow_instance_id IS NOT NULL AND scope_id IS NOT NULL"),
            postgresql_where=text("airflow_instance_id IS NOT NULL AND scope_id IS NOT NULL"),
        ),
        Index(
            "uq_airflow_attachments_dag_space",
            "dag_row_id",
            "space_id",
            unique=True,
            sqlite_where=text("dag_row_id IS NOT NULL AND space_id IS NOT NULL"),
            postgresql_where=text("dag_row_id IS NOT NULL AND space_id IS NOT NULL"),
        ),
        Index(
            "uq_airflow_attachments_dag_scope",
            "dag_row_id",
            "scope_id",
            unique=True,
            sqlite_where=text("dag_row_id IS NOT NULL AND scope_id IS NOT NULL"),
            postgresql_where=text("dag_row_id IS NOT NULL AND scope_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    airflow_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airflow_instances.id", ondelete="CASCADE"),
        nullable=True,
    )
    dag_row_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airflow_dags.id", ondelete="CASCADE"),
        nullable=True,
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("spaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scopes.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AirflowInstanceKnowledgeContextRow(Base):
    """Latest built analysis context for one Airflow instance. Findings are keyed by DAG."""

    __tablename__: str = "airflow_instance_knowledge_contexts"
    __table_args__: tuple[UniqueConstraint, ...] = (
        UniqueConstraint("instance_id", name="uq_airflow_instance_knowledge_contexts_instance"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airflow_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AirflowSyncRunRow(Base):
    """One DAG snapshot sync against an Airflow instance."""

    __tablename__: str = "airflow_sync_runs"
    __table_args__: tuple[CheckConstraint, ...] = (
        _enum_check(
            "status",
            tuple(item.value for item in SyncRunStatus),
            "ck_airflow_sync_runs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airflow_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    counts: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    sanitized_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncScheduleRow(Base):
    """Interval for one Trino binding or one Airflow instance. Beat claims due rows."""

    __tablename__: str = "sync_schedules"
    __table_args__: tuple[UniqueConstraint | CheckConstraint, ...] = (
        UniqueConstraint("source_engine_binding_id", name="uq_sync_schedules_binding"),
        UniqueConstraint("airflow_instance_id", name="uq_sync_schedules_instance"),
        CheckConstraint(
            "(source_engine_binding_id IS NOT NULL AND airflow_instance_id IS NULL) OR "
            "(source_engine_binding_id IS NULL AND airflow_instance_id IS NOT NULL)",
            name="ck_sync_schedules_one_target",
        ),
        CheckConstraint("interval_seconds >= 1", name="ck_sync_schedules_interval"),
        CheckConstraint(
            "model IN ('llama3.2', 'qwen3:4b', 'gpt-4.1', 'gpt-4.1-mini', 'gpt-6-astra')",
            name="ck_sync_schedules_model",
        ),
        CheckConstraint(
            "effort IN ('off', 'minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_sync_schedules_effort",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_engine_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_engine_bindings.id", ondelete="CASCADE"),
        nullable=True,
    )
    airflow_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("airflow_instances.id", ondelete="CASCADE"),
        nullable=True,
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="gpt-4.1",
        server_default=text("'gpt-4.1'"),
    )
    effort: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="low",
        server_default=text("'low'"),
    )
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CatalogJobRow(Base):
    """One background build. Status is shared by the API process and the Celery worker."""

    __tablename__: str = "catalog_jobs"
    __table_args__: tuple[CheckConstraint | Index, ...] = (
        _enum_check(
            "kind",
            tuple(item.value for item in CatalogJobKind),
            "ck_catalog_jobs_kind",
        ),
        _enum_check(
            "status",
            tuple(item.value for item in CatalogJobStatus),
            "ck_catalog_jobs_status",
        ),
        Index(
            "uq_catalog_jobs_running",
            "kind",
            "subject_id",
            "lane",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    lane: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


_ONE_CONFIGURATION: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL AND catalog_table_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NOT NULL AND catalog_table_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NULL AND catalog_table_id IS NOT NULL)"
)


class EntityConfigurationRow(Base):
    """Saved refresh interval and build options for one space, scope, or table."""

    __tablename__: str = "entity_configurations"
    __table_args__: tuple[UniqueConstraint | CheckConstraint, ...] = (
        UniqueConstraint("space_id", name="uq_entity_configurations_space"),
        UniqueConstraint("scope_id", name="uq_entity_configurations_scope"),
        UniqueConstraint("catalog_table_id", name="uq_entity_configurations_table"),
        CheckConstraint(_ONE_CONFIGURATION, name="ck_entity_configurations_one_subject"),
        CheckConstraint("interval_seconds >= 1", name="ck_entity_configurations_interval"),
        CheckConstraint(
            "model IN ('llama3.2', 'qwen3:4b', 'gpt-4.1', 'gpt-4.1-mini', 'gpt-6-astra')",
            name="ck_entity_configurations_model",
        ),
        CheckConstraint(
            "effort IN ('off', 'minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_entity_configurations_effort",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("spaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scopes.id", ondelete="CASCADE"),
        nullable=True,
    )
    catalog_table_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("catalog_tables.id", ondelete="CASCADE"),
        nullable=True,
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="gpt-4.1",
        server_default=text("'gpt-4.1'"),
    )
    effort: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="low",
        server_default=text("'low'"),
    )
    options: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
