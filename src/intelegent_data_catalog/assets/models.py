"""API and agent models for catalog assets."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from intelegent_data_catalog.core.settings import ModelId, ReasoningEffort
from intelegent_data_catalog.persistence.enums import (
    AssetKind,
    AssetRefreshMode,
    AssetStatus,
    ContextOrigin,
)

AssetSort = Literal[
    "name",
    "kind",
    "updated_at",
    "last_built_at",
    "membership_count",
    "freshness_score",
    "context_state",
]
AssetOwnerFilter = Literal["me", "shared", "all"]
ContextState = Literal["none", "user", "llm"]
MembershipTarget = Literal["scope", "space"]
MembershipAction = Literal["attach", "detach"]


class AssetContextOptions(BaseModel):
    """Model profile and kind-specific flags for one asset build."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: ModelId = "gpt-4.1"
    effort: ReasoningEffort = "low"
    flags: dict[str, bool] = Field(default_factory=dict)


class AssetAnalysisContext(BaseModel):
    """Latest analysis snapshot for one asset."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    kind: AssetKind
    summary: str | None = None
    built_at: datetime
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class AssetMembershipRead(BaseModel):
    """One space or scope that holds an asset."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    asset_id: UUID
    space_id: UUID | None
    scope_id: UUID | None
    created_at: datetime


class AssetTargetRef(BaseModel):
    """A space or scope name shown on an asset row."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str


class AssetUpdate(BaseModel):
    """User edits stored on the asset row."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    context: str | None = None
    status: AssetStatus | None = None


class AssetRead(BaseModel):
    """One asset plus the memberships and a kind-specific detail payload."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: AssetKind
    name: str
    slug: str
    description: str | None
    context: str | None
    context_origin: ContextOrigin | None
    workspace_id: UUID
    owner_user_id: UUID | None
    status: AssetStatus
    properties: dict[str, JsonValue]
    tags: list[str] = Field(default_factory=list)
    memberships: list[AssetMembershipRead]
    detail: dict[str, JsonValue]
    created_at: datetime
    updated_at: datetime
    last_synced_at: datetime | None


class AssetListItem(BaseModel):
    """One row in the assets table, including rank fields."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: AssetKind
    name: str
    slug: str
    description: str | None
    workspace_id: UUID
    owner_user_id: UUID | None
    status: AssetStatus
    subtitle: str | None
    membership_count: int
    scopes: list[AssetTargetRef]
    spaces: list[AssetTargetRef]
    last_built_at: datetime | None
    context_state: ContextState
    freshness_score: int
    build_running: bool
    refresh_mode: AssetRefreshMode
    interval_seconds: int
    schedule: str | None = None
    next_run_at: datetime | None = None
    updated_at: datetime


class AssetPage(BaseModel):
    """One page of the asset search."""

    model_config = ConfigDict(extra="forbid")

    items: list[AssetListItem]
    total: int


class AssetQuery(BaseModel):
    """Filters, rank, and page for ``GET /assets``."""

    model_config = ConfigDict(extra="forbid")

    q: str | None = None
    kinds: list[AssetKind] = Field(default_factory=list)
    owner: AssetOwnerFilter = "all"
    workspace_id: UUID | None = None
    scope_id: UUID | None = None
    space_id: UUID | None = None
    status: AssetStatus | None = None
    has_context: bool | None = None
    refresh_mode: AssetRefreshMode | None = None
    sort: AssetSort = "updated_at"
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class BulkMembershipWrite(BaseModel):
    """Attach or detach many assets at one space or scope."""

    model_config = ConfigDict(extra="forbid")

    asset_ids: list[UUID] = Field(min_length=1, max_length=200)
    target_kind: MembershipTarget
    target_id: UUID
    action: MembershipAction


class BulkMembershipResult(BaseModel):
    """How many memberships changed. Already-applied rows are unchanged."""

    model_config = ConfigDict(extra="forbid")

    changed: int
    unchanged: int


class AssetIdsWrite(BaseModel):
    """Asset ids to attach to one scope or space."""

    model_config = ConfigDict(extra="forbid")

    asset_ids: list[UUID] = Field(min_length=1, max_length=200)


class FileCatalogContext(BaseModel):
    """Text extracted from a file or notebook before the model writes context."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    name: str
    kind: AssetKind
    content_type: str | None
    text: str
    referenced_tables: list[str] = Field(default_factory=list)


class FileContextProposal(BaseModel):
    """Business context the model wrote for a file or notebook."""

    model_config = ConfigDict(extra="ignore")

    context: str
    summary: str = ""


class DagDescribeInput(BaseModel):
    """One DAG sent to the per-DAG describer."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    dag_id: str
    description: str | None
    source_code: str | None
    task_ids: list[str] = Field(default_factory=list)
