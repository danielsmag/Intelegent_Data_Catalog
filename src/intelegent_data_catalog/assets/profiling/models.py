"""Typed profile stored as an asset output and returned by the profile API."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PROFILE_TASK_IDS: frozenset[str] = frozenset(
    {
        "iceberg_table_profile",
        "iceberg_schema_profile",
        "iceberg_catalog_profile",
    },
)


class ProfileScope(StrEnum):
    """How wide a profile document is."""

    TABLE = "table"
    SCHEMA = "schema"
    CATALOG = "catalog"


class ColumnLinkKind(StrEnum):
    """Why two columns were linked."""

    CANDIDATE_REFERENCE = "CANDIDATE_REFERENCE"
    SAME_NAME = "SAME_NAME"


class ColumnProfile(BaseModel):
    """Statistics for one primitive column."""

    model_config = ConfigDict(extra="forbid")

    name: str
    iceberg_type: str
    null_count: int | None = Field(default=None, ge=0)
    null_ratio: float | None = Field(default=None, ge=0, le=1)
    distinct_count: int | None = Field(default=None, ge=0)
    uniqueness_ratio: float | None = Field(default=None, ge=0, le=1)
    minimum: str | None = None
    maximum: str | None = None
    mean: float | None = None
    stddev: float | None = None
    avg_length: float | None = None
    max_length: int | None = Field(default=None, ge=0)
    true_count: int | None = Field(default=None, ge=0)
    candidate_key: bool = False


class TableProfile(BaseModel):
    """Statistics for one Iceberg table."""

    model_config = ConfigDict(extra="forbid")

    catalog: str
    namespace: str
    table: str
    row_count: int | None = Field(default=None, ge=0)
    columns: list[ColumnProfile]
    columns_truncated: bool = False
    sql: str
    profiled_at: datetime


class ColumnLink(BaseModel):
    """One inferred link. Endpoints are ``catalog.namespace.table.column``."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    kind: ColumnLinkKind
    score: float = Field(ge=0, le=1)
    reason: str


class ProfileGraphStatus(BaseModel):
    """Whether the Neo4j profile subgraph was replaced."""

    model_config = ConfigDict(extra="forbid")

    written: bool = False
    error: str | None = None


class ProfileDocument(BaseModel):
    """One profile run. The JSON sidecar and the profile API share this shape."""

    model_config = ConfigDict(extra="forbid")

    scope: ProfileScope
    scope_name: str
    tables: list[TableProfile] = Field(default_factory=list)
    links: list[ColumnLink] = Field(default_factory=list)
    tables_truncated: bool = False
    columns_truncated: bool = False
    namespaces_truncated: bool = False
    skipped: list[str] = Field(default_factory=list)
    graph: ProfileGraphStatus = Field(default_factory=ProfileGraphStatus)


class AssetProfileRead(BaseModel):
    """Newest profile for one asset."""

    model_config = ConfigDict(extra="forbid")

    output_id: UUID
    asset_id: UUID
    run_id: UUID
    created_at: datetime
    document: ProfileDocument
    markdown: str


def column_fqn(catalog: str, namespace: str, table: str, column: str) -> str:
    """Return the column id used by links and the profile graph."""
    return f"{catalog}.{namespace}.{table}.{column}"


def table_fqn(catalog: str, namespace: str, table: str) -> str:
    """Return the table id used by the profile graph."""
    return f"{catalog}.{namespace}.{table}"
