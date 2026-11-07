"""Pure declarations produced by a definition file, plus scan API models."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from intelegent_data_catalog.persistence.enums import (
    AssetStatus,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
)

DefinitionFolder = Literal["assets"]


class TableSourceRef(BaseModel):
    """Catalog table named by source key."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["table"] = "table"
    source_key: str = Field(min_length=1)
    data_source: str | None = None


class AirflowSourceRef(BaseModel):
    """Airflow DAG named by instance slug and dag id."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["airflow"] = "airflow"
    instance: str = Field(min_length=1)
    dag_id: str = Field(min_length=1, max_length=250)


class AirflowInstanceSourceRef(BaseModel):
    """Registered Airflow instance named by slug."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["airflow_instance"] = "airflow_instance"
    instance: str = Field(min_length=1)


class PageSourceRef(BaseModel):
    """Workspace file or notebook named by workspace slug and path."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["page"] = "page"
    workspace: str = Field(min_length=1)
    path: str = Field(min_length=1, max_length=2048)


class SchemaSourceRef(BaseModel):
    """Every catalog table in one physical catalog and namespace.

    ``include`` limits the names. ``None`` and an empty list mean the whole schema.
    ``exclude`` drops names after ``include``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["schema"] = "schema"
    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    data_source: str | None = None
    include: list[str] | None = None
    exclude: list[str] = Field(default_factory=list)


SourceRef = Annotated[
    TableSourceRef | AirflowSourceRef | AirflowInstanceSourceRef | PageSourceRef | SchemaSourceRef,
    Field(discriminator="kind"),
]


class OperatorRef(BaseModel):
    """Operator class name and fields as the definition file wrote them.

    An executor rebuilds the operator from this and calls ``execute``.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=255)
    params: dict[str, JsonValue] = Field(default_factory=dict)


class AssetSpec(BaseModel):
    """One asset declaration.

    ``operator`` is absent only on items stored before operators ran. ``schedule``
    is a five-field cron, or null when the operator runs once on first apply.
    ``key`` identifies the declaration and is the visible name. ``name`` stays
    on stored items from older files and is ignored.
    ``tags`` lists labels the file passed. An empty list with ``tags`` absent from
    ``set_fields`` means the file did not mention tags.
    ``parent_key`` is set on a table generated from a schema declaration.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    source: SourceRef
    operator: OperatorRef | None = None
    parent_key: str | None = None
    schedule: str | None = None
    name: str | None = None
    description: str | None = None
    context: str | None = None
    status: AssetStatus | None = None
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    set_fields: list[str] = Field(default_factory=list)

    @field_validator("schedule")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from intelegent_data_catalog.schedual.cron import parse_schedule

        return parse_schedule(value)


class DefinitionSpec(BaseModel):
    """Everything one definition file declared."""

    model_config = ConfigDict(extra="forbid")

    assets: list[AssetSpec] = Field(default_factory=list)


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
    """One declared object and the catalog row it produced."""

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
    """A definition file, its script, and the objects it declared."""

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
    """One object that exists under a definition folder."""

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
