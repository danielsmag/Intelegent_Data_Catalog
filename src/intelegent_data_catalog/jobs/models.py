"""Read models for a background catalog job."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.knowledge.models import ContextSubjectKind

KnowledgeBuildState = Literal["running", "succeeded", "failed"]


class KnowledgeBuildJobRead(BaseModel):
    """One background context build. The saved context is a separate read."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    table_id: UUID
    table_name: str
    status: KnowledgeBuildState
    error: str | None = None


class ScopeBuildJobRead(BaseModel):
    """One background scope build. The saved context is a separate read."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    scope_id: UUID
    scope_name: str
    status: KnowledgeBuildState
    error: str | None = None


class SpaceBuildJobRead(BaseModel):
    """One background space build. The saved context is a separate read."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    space_id: UUID
    space_name: str
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


class ContextGenerationJobRead(BaseModel):
    """One background context write. The catalog row is a separate read."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    subject_kind: ContextSubjectKind
    subject_id: UUID
    subject_name: str
    status: KnowledgeBuildState
    error: str | None = None
