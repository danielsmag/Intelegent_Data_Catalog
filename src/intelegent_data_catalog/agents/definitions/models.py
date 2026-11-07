"""Pure declarations produced by a definition file, plus scan API models."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from intelegent_data_catalog.persistence.enums import (
    AgentStatus,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
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


class OperatorRef(BaseModel):
    """Operator class name and fields as the definition file wrote them.

    An executor rebuilds the operator from this and calls ``execute``.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=255)
    params: dict[str, JsonValue] = Field(default_factory=dict)


class AgentSpec(BaseModel):
    """One agent declaration.

    ``operators`` run in order. An empty list resolves the agent and runs nothing.
    A stored ``operator`` from an older file is folded into ``operators`` so the
    row still loads. ``schedule`` is a five-field cron, or null when the steps
    run once on first apply.
    ``key`` identifies the declaration and is the visible name. ``name`` stays
    on stored items from older files and is ignored.
    ``tags`` lists labels the file passed. An empty list with ``tags`` absent from
    ``set_fields`` means the file did not mention tags.
    ``pack`` caps the agent files stored beside the declaration.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    source: SourceRef
    operators: list[OperatorRef] = Field(default_factory=list)
    pack: PackSpec | None = None
    schedule: str | None = None
    name: str | None = None
    description: str | None = None
    context: str | None = None
    status: AgentStatus | None = None
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    set_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fold_stored_operator(cls, value: object) -> object:
        """Accept a stored single ``operator`` and drop a schema ``parent_key``."""
        if not isinstance(value, dict):
            return value
        data: dict[object, object] = dict(value)
        data.pop("parent_key", None)
        if "operators" not in data and "operator" in data:
            stored: object = data.pop("operator")
            data["operators"] = [] if stored is None else [stored]
        else:
            data.pop("operator", None)
        return data

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
    run_status: DefinitionRunStatus | None = None
    run_error: str | None = None
    ran_at: datetime | None = None
    schedule: str | None = None
    next_run_at: datetime | None = None


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
