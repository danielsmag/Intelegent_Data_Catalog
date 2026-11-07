"""Limits for the markdown files stored beside one agent."""

from pydantic import BaseModel, ConfigDict, Field


class AgentPack(BaseModel):
    """Caps for one agent's prompt, rule, and skill files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_files: int = Field(default=40, ge=1, le=200)
    single_active_prompt: bool = True
