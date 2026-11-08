"""Pure declarations produced by an asset definition file, plus scan API models."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from intelegent_data_catalog.persistence.enums import (
    AssetKind,
    AssetStatus,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
)
from intelegent_data_catalog.schedual.cron import ScheduleError, parse_schedule

DefinitionFolder = Literal["assets"]


class AirflowInstanceSourceRef(BaseModel):
    """Registered Airflow instance named by slug."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["airflow_instance"] = "airflow_instance"
    instance: str = Field(min_length=1, max_length=255)


class IcebergTableSourceRef(BaseModel):
    """One Iceberg table in a Polaris catalog."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["iceberg_table"] = "iceberg_table"
    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    table: str = Field(min_length=1, max_length=512)


class IcebergCatalogSourceRef(BaseModel):
    """Every namespace in one Iceberg catalog.

    ``include`` limits namespace names. ``None`` and an empty list mean the whole catalog.
    ``exclude`` drops names after ``include``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["iceberg_catalog"] = "iceberg_catalog"
    catalog: str = Field(min_length=1, max_length=512)
    include: list[str] | None = None
    exclude: list[str] = Field(default_factory=list)


class IcebergSchemaSourceRef(BaseModel):
    """Every table in one Iceberg namespace.

    ``include`` limits the names. ``None`` and an empty list mean the whole namespace.
    ``exclude`` drops names after ``include``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["iceberg_schema"] = "iceberg_schema"
    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    include: list[str] | None = None
    exclude: list[str] = Field(default_factory=list)


class GitHubRepoSourceRef(BaseModel):
    """One GitHub repository and the branch whose new commits rerun the asset."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["github_repo"] = "github_repo"
    owner: str = Field(min_length=1, max_length=100)
    repo: str = Field(min_length=1, max_length=100)
    branch: str = Field(default="master", min_length=1, max_length=255)


SourceRef = Annotated[
    AirflowInstanceSourceRef
    | IcebergTableSourceRef
    | IcebergSchemaSourceRef
    | IcebergCatalogSourceRef
    | GitHubRepoSourceRef,
    Field(discriminator="kind"),
]


class OperatorRef(BaseModel):
    """Operator class name and the fields the definition file set."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=255)
    params: dict[str, JsonValue] = Field(default_factory=dict)


class AssetSpec(BaseModel):
    """One asset declaration.

    ``operators`` run in order. An empty list resolves the asset and runs nothing.
    ``schedule`` is a five-field cron, or null when the steps run once on first apply.
    ``key`` identifies the declaration and is the visible name.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    source: SourceRef
    operators: list[OperatorRef] = Field(default_factory=list)
    schedule: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    set_fields: list[str] = Field(default_factory=list)

    @field_validator("schedule")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return parse_schedule(value)
        except ScheduleError as err:
            raise ValueError(str(err)) from err


class DefinitionSpec(BaseModel):
    """Everything one definition file declared."""

    model_config = ConfigDict(extra="forbid")

    assets: list[AssetSpec] = Field(default_factory=list)


def asset_kind_for(source: SourceRef) -> AssetKind:
    """Return the stored kind for a source reference."""
    match source:
        case AirflowInstanceSourceRef():
            return AssetKind.AIRFLOW_INSTANCE
        case IcebergTableSourceRef():
            return AssetKind.ICEBERG_TABLE
        case IcebergSchemaSourceRef():
            return AssetKind.ICEBERG_SCHEMA
        case IcebergCatalogSourceRef():
            return AssetKind.ICEBERG_CATALOG
        case GitHubRepoSourceRef():
            return AssetKind.GITHUB_REPO


class AssetDefinitionFileRead(BaseModel):
    """One scanned definition file."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    key: str
    folder_kind: DefinitionKind
    status: DefinitionFileStatus
    error: str | None
    item_count: int
    etag: str
    size_bytes: int
    scanned_at: datetime | None
    parsed_at: datetime | None


class AssetDefinitionItemRead(BaseModel):
    """One declared asset and the catalog row it produced."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: DefinitionKind
    item_key: str
    status: DefinitionItemStatus
    error: str | None
    asset_id: UUID | None
    run_status: DefinitionRunStatus | None = None
    run_error: str | None = None
    ran_at: datetime | None = None
    schedule: str | None = None
    next_run_at: datetime | None = None


class AssetDefinitionFileDetail(BaseModel):
    """A definition file, its script, and the assets it declared."""

    model_config = ConfigDict(extra="forbid")

    file: AssetDefinitionFileRead
    source: str = ""
    items: list[AssetDefinitionItemRead]


class AssetDefinitionTextRead(BaseModel):
    """Script text of one object in the definition store."""

    model_config = ConfigDict(extra="forbid")

    key: str
    source: str


class AssetDefinitionScanQueued(BaseModel):
    """Manual scan accepted."""

    model_config = ConfigDict(extra="forbid")

    queued: bool


class AssetDefinitionObjectRead(BaseModel):
    """One object that exists under the assets folder."""

    model_config = ConfigDict(extra="forbid")

    key: str
    path: str
    size_bytes: int
    last_modified: datetime | None
    file_id: UUID | None = None
    asset_ids: list[UUID] = Field(default_factory=list)


class ScanResult(BaseModel):
    """Counts from one pass over the definition prefix."""

    model_config = ConfigDict(extra="forbid")

    seen: int
    parsed: int
    removed: int
    errors: int
    retried: int


class AssetRead(BaseModel):
    """One asset and the schedule of its applied declaration."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: AssetKind
    key: str
    name: str
    description: str | None
    status: AssetStatus
    source: dict[str, JsonValue]
    properties: dict[str, JsonValue]
    schedule: str | None
    next_run_at: datetime | None
    run_status: DefinitionRunStatus | None
    ran_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AssetPage(BaseModel):
    """One page of assets."""

    model_config = ConfigDict(extra="forbid")

    items: list[AssetRead]
    total: int


class AssetRunQueued(BaseModel):
    """Manual run accepted."""

    model_config = ConfigDict(extra="forbid")

    queued: bool
