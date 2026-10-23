"""Read models for asset operator runs."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from intelegent_data_catalog.persistence.enums import (
    DefinitionRunStatus,
    OperatorRunTrigger,
    OperatorTaskState,
)


class AssetRunEventRead(BaseModel):
    """One build-trace step stored on a task."""

    model_config = ConfigDict(extra="ignore")

    at: datetime
    step: str
    title: str
    detail: str


class AssetRunTaskSummary(BaseModel):
    """Task state shown in the run grid. The log stays on the detail read."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    state: OperatorTaskState
    started_at: datetime | None
    finished_at: datetime | None
    duration: float | None
    error: str | None


class AssetRunRead(BaseModel):
    """One stored run and the state of its tasks."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    asset_id: UUID
    item_id: UUID | None
    catalog_job_id: UUID | None
    status: DefinitionRunStatus
    trigger: OperatorRunTrigger
    try_number: int
    operator_type: str | None
    operator_params: dict[str, JsonValue]
    error: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration: float | None
    tasks: list[AssetRunTaskSummary]


class AssetRunTaskDetail(BaseModel):
    """One task, its log, and the run fields the detail panel shows."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    task_id: str
    state: OperatorTaskState
    started_at: datetime | None
    finished_at: datetime | None
    duration: float | None
    error: str | None
    log: str
    events: list[AssetRunEventRead]
    trigger: OperatorRunTrigger
    try_number: int
    operator_type: str | None
    operator_params: dict[str, JsonValue]
    run_status: DefinitionRunStatus
    run_error: str | None
