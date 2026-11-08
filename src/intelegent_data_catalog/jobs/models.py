"""Read models for a background catalog job."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.knowledge.models import ContextSubjectKind

KnowledgeBuildState = Literal["running", "succeeded", "failed", "waiting_approval"]


class KnowledgeBuildJobRead(BaseModel):
    """One background context build. The saved context is a separate read."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    table_id: UUID
    table_name: str
    status: KnowledgeBuildState
    error: str | None = None


class AgentBuildJobRead(BaseModel):
    """One background agent build. The saved context is a separate read."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    agent_id: UUID
    agent_name: str
    status: KnowledgeBuildState
    error: str | None = None


class AirflowBuildJobRead(BaseModel):
    """One background Airflow build. The saved context is a separate read."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    instance_id: UUID
    instance_name: str
    status: KnowledgeBuildState
    error: str | None = None


class JobApprovalRead(BaseModel):
    """The step a paused build is waiting on."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: KnowledgeBuildState
    pending: dict[str, object] | None = None


class JobApprovalWrite(BaseModel):
    """Approve, reject, or replace the paused proposal."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    output: dict[str, object] | None = None


class CatalogJobCancelRead(BaseModel):
    """Result of cancelling one background build."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: KnowledgeBuildState
    error: str | None = None


class ContextGenerationJobRead(BaseModel):
    """One background context write. The catalog row is a separate read."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    subject_kind: ContextSubjectKind
    subject_id: UUID
    subject_name: str
    status: KnowledgeBuildState
    error: str | None = None
