"""Pure declarations produced by a definition file, plus scan API models."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from intelegent_data_catalog.persistence.enums import (
    AssetStatus,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
)

DefinitionFolder = Literal["spaces", "scopes", "assets"]


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


class PageSourceRef(BaseModel):
    """Workspace file or notebook named by workspace slug and path."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["page"] = "page"
    workspace: str = Field(min_length=1)
    path: str = Field(min_length=1, max_length=2048)


SourceRef = Annotated[
    TableSourceRef | AirflowSourceRef | PageSourceRef,
    Field(discriminator="kind"),
]


class SpaceSpec(BaseModel):
    """One space declaration. ``set_fields`` lists arguments the file passed."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    description: str | None = None
    context: str | None = None
    set_fields: list[str] = Field(default_factory=list)


class ScopeSpec(BaseModel):
    """One scope declaration inside a space slug."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    space_slug: str
    name: str | None = None
    description: str | None = None
    context: str | None = None
    set_fields: list[str] = Field(default_factory=list)


class AssetSpec(BaseModel):
    """One asset declaration. Exactly one of ``scope`` or ``space`` is set."""

    model_config = ConfigDict(extra="forbid")

    key: str
    source: SourceRef
    scope: str | None = None
    space: str | None = None
    name: str | None = None
    description: str | None = None
    context: str | None = None
    status: AssetStatus | None = None
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    set_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_target(self) -> AssetSpec:
        if (self.scope is None) == (self.space is None):
            raise ValueError("asset needs exactly one of scope or space")
        return self


class DefinitionSpec(BaseModel):
    """Everything one definition file declared."""

    model_config = ConfigDict(extra="forbid")

    spaces: list[SpaceSpec] = Field(default_factory=list)
    scopes: list[ScopeSpec] = Field(default_factory=list)
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
    space_id: UUID | None
    scope_id: UUID | None
    asset_id: UUID | None


class AssetDefinitionFileDetail(BaseModel):
    """A definition file plus the objects it declared."""

    model_config = ConfigDict(extra="forbid")

    file: AssetDefinitionFileRead
    items: list[AssetDefinitionItemRead]


class AssetDefinitionScanQueued(BaseModel):
    """Manual scan accepted."""

    model_config = ConfigDict(extra="forbid")

    queued: bool


class ScanResult(BaseModel):
    """Counts from one pass over the definition prefix."""

    model_config = ConfigDict(extra="forbid")

    seen: int
    parsed: int
    removed: int
    errors: int
    retried: int
