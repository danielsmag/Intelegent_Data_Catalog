"""Pure declarations produced by a definition file, plus scan API models."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from intelegent_data_catalog.persistence.enums import (
    AgentStatus,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
)

DefinitionFolder = Literal["agents"]


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


class PackSpec(BaseModel):
    """Caps for the agent files stored beside one declaration."""

    model_config = ConfigDict(extra="forbid")

    max_files: int = Field(default=40, ge=1, le=200)
    single_active_prompt: bool = True


class AgentSpec(BaseModel):
    """One agent declaration.

    ``key`` identifies the declaration and is the visible name. ``name`` stays
    on stored items from older files and is ignored.
    ``tags`` lists labels the file passed. An empty list with ``tags`` absent from
    ``set_fields`` means the file did not mention tags.
    ``pack`` caps the agent files stored beside the declaration.
    A stored ``operator`` or ``schedule`` from an older file is ignored.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    source: SourceRef
    pack: PackSpec | None = None
    name: str | None = None
    description: str | None = None
    context: str | None = None
    status: AgentStatus | None = None
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    set_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_fields(cls, value: object) -> object:
        """Ignore operator and schedule fields left by older definition files."""
        if not isinstance(value, dict):
            return value
        data: dict[object, object] = dict(value)
        data.pop("parent_key", None)
        data.pop("operator", None)
        data.pop("operators", None)
        data.pop("schedule", None)
        return data


class DefinitionSpec(BaseModel):
    """Everything one definition file declared."""

    model_config = ConfigDict(extra="forbid")

    agents: list[AgentSpec] = Field(default_factory=list)


class AgentDefinitionFileRead(BaseModel):
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


class AgentDefinitionItemRead(BaseModel):
    """One declared object and the catalog row it produced."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: DefinitionKind
    item_key: str
    status: DefinitionItemStatus
    error: str | None
    agent_id: UUID | None


class AgentDefinitionFileDetail(BaseModel):
    """A definition file, its script, and the objects it declared."""

    model_config = ConfigDict(extra="forbid")

    file: AgentDefinitionFileRead
    source: str = ""
    items: list[AgentDefinitionItemRead]


class AgentDefinitionTextRead(BaseModel):
    """Script text of one object in the definition store."""

    model_config = ConfigDict(extra="forbid")

    key: str
    source: str


class AgentDefinitionScanQueued(BaseModel):
    """Manual scan accepted."""

    model_config = ConfigDict(extra="forbid")

    queued: bool


class AgentDefinitionObjectRead(BaseModel):
    """One object that exists under a definition folder."""

    model_config = ConfigDict(extra="forbid")

    key: str
    path: str
    size_bytes: int
    last_modified: datetime | None
    file_id: UUID | None = None
    agent_ids: list[UUID] = Field(default_factory=list)


class ScanResult(BaseModel):
    """Counts from one pass over the definition prefix."""

    model_config = ConfigDict(extra="forbid")

    seen: int
    parsed: int
    removed: int
    errors: int
    retried: int
