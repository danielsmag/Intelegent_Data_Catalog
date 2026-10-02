"""Discovery shapes shared by Trino and metadata sync."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from intelegent_data_catalog.persistence.enums import CapabilitySupport, TableKind


class SourceColumnRecord(BaseModel):
    """One column observed from a discovery engine."""

    model_config = ConfigDict(extra="forbid")

    name: str
    discovered_data_type: str | None = None
    nullable: bool | None = None
    ordinal_position: int = Field(ge=0)
    source_description: str | None = None


class SourceTableRecord(BaseModel):
    """One table observed from a discovery engine."""

    model_config = ConfigDict(extra="forbid")

    catalog: str
    namespace: str
    name: str
    table_type: TableKind
    source_description: str | None = None
    columns: list[SourceColumnRecord]


class ImportedForeignKey(BaseModel):
    """One column of a foreign key reported by a source that supports constraints."""

    model_config = ConfigDict(extra="forbid")

    constraint_name: str
    source_namespace: str
    source_table: str
    source_column: str
    target_namespace: str
    target_table: str
    target_column: str
    position: int = Field(ge=0)


class DiscoveryBatch(BaseModel):
    """Tables and constraint support from one discovery call.

    ``relationship_capability`` UNKNOWN or UNSUPPORTED is not an empty foreign-key set.
    """

    model_config = ConfigDict(extra="forbid")

    tables: list[SourceTableRecord]
    relationship_capability: CapabilitySupport
    relationships: list[ImportedForeignKey] = Field(default_factory=list)
    truncated: bool = False


class DiscoveryRequest(BaseModel):
    """Approved catalog and schema allowlists for one source binding."""

    model_config = ConfigDict(extra="forbid")

    catalog: str
    schema_allowlist: list[str] = Field(default_factory=list)
    include_system: bool = False
    max_tables: int = Field(default=500, ge=1)


class EnrichmentBatch(BaseModel):
    """Native metadata used only when Trino did not report that capability."""

    model_config = ConfigDict(extra="forbid")

    relationship_capability: CapabilitySupport
    relationships: list[ImportedForeignKey] = Field(default_factory=list)
    native_types: dict[str, str] = Field(default_factory=dict)


class ScanStop(StrEnum):
    """Why a discovery read stopped."""

    COMPLETE = "COMPLETE"
    TRUNCATED = "TRUNCATED"
    FAILED = "FAILED"
