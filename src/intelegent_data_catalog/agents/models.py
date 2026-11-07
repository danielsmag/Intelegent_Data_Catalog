"""API and agent models for catalog agents."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from intelegent_data_catalog.core.settings import ModelId, ReasoningEffort
from intelegent_data_catalog.persistence.enums import (
    AgentKind,
    AgentRefreshMode,
    AgentStatus,
    ContextOrigin,
)

AgentSort = Literal[
    "name",
    "kind",
    "updated_at",
    "last_built_at",
    "freshness_score",
    "context_state",
]
AgentOwnerFilter = Literal["me", "shared", "all"]
ContextState = Literal["none", "user", "llm"]


class AgentContextOptions(BaseModel):
    """Model profile and kind-specific flags for one agent build."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: ModelId = "gpt-4.1"
    effort: ReasoningEffort = "low"
    flags: dict[str, bool] = Field(default_factory=dict)


class AgentAnalysisContext(BaseModel):
    """Latest analysis snapshot for one agent."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    kind: AgentKind
    summary: str | None = None
    built_at: datetime
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    """User edits stored on the agent row."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    context: str | None = None
    status: AgentStatus | None = None


class AgentRead(BaseModel):
    """One agent plus a kind-specific detail payload."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: AgentKind
    name: str
    slug: str
    description: str | None
    context: str | None
    context_origin: ContextOrigin | None
    workspace_id: UUID
    owner_user_id: UUID | None
    status: AgentStatus
    properties: dict[str, JsonValue]
    tags: list[str] = Field(default_factory=list)
    detail: dict[str, JsonValue]
    created_at: datetime
    updated_at: datetime
    last_synced_at: datetime | None


class AgentListItem(BaseModel):
    """One row in the agents table, including rank fields."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: AgentKind
    name: str
    slug: str
    description: str | None
    workspace_id: UUID
    owner_user_id: UUID | None
    status: AgentStatus
    subtitle: str | None
    table_format: str | None = None
    last_built_at: datetime | None
    context_state: ContextState
    freshness_score: int
    build_running: bool
    refresh_mode: AgentRefreshMode
    interval_seconds: int
    schedule: str | None = None
    next_run_at: datetime | None = None
    updated_at: datetime


class AgentPage(BaseModel):
    """One page of the agent search."""

    model_config = ConfigDict(extra="forbid")

    items: list[AgentListItem]
    total: int


class AgentQuery(BaseModel):
    """Filters, rank, and page for ``GET /agents``."""

    model_config = ConfigDict(extra="forbid")

    q: str | None = None
    kinds: list[AgentKind] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    owner: AgentOwnerFilter = "all"
    workspace_id: UUID | None = None
    status: AgentStatus | None = None
    has_context: bool | None = None
    refresh_mode: AgentRefreshMode | None = None
    sort: AgentSort = "updated_at"
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class FileCatalogContext(BaseModel):
    """Text extracted from a file or notebook before the model writes context."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    name: str
    kind: AgentKind
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

    agent_id: UUID
    dag_id: str
    description: str | None
    source_code: str | None
    task_ids: list[str] = Field(default_factory=list)
