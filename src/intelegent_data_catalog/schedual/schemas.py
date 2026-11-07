"""Schedule bodies for the catalog API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from intelegent_data_catalog.core.settings import ModelId, ReasoningEffort


class SyncScheduleWrite(BaseModel):
    """Create or replace the interval for one sync target."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(ge=1, le=2_147_483_647)
    enabled: bool
    model: ModelId = "gpt-4.1"
    effort: ReasoningEffort = "low"


class SyncScheduleRead(BaseModel):
    """One stored sync interval."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_engine_binding_id: UUID | None
    airflow_instance_id: UUID | None
    interval_seconds: int
    enabled: bool
    model: ModelId
    effort: ReasoningEffort
    next_run_at: datetime
