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
