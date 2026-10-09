"""Read models for a background catalog job."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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
