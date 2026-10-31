"""HTTP models for Airflow instances. Passwords are accepted and never returned."""

from datetime import datetime
from typing import Literal
from urllib.parse import SplitResult, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from intelegent_data_catalog.persistence.enums import (
    AirflowInstanceStatus,
    AirflowMappingDirection,
    AirflowMappingOrigin,
    ContextOrigin,
    SyncRunStatus,
)
from intelegent_data_catalog.registry.secrets import reject_endpoint_credentials


def http_base_url(value: str) -> str:
    """Accept an http(s) Airflow base URL with no embedded credentials."""
    cleaned: str = reject_endpoint_credentials(value.strip()).rstrip("/")
    parsed: SplitResult = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        raise ValueError("base_url must be an http or https URL")
    return cleaned


def _non_blank(value: str) -> str:
    if value.strip() == "":
        raise ValueError("cannot be blank")
    return value


class AirflowInstanceCreate(BaseModel):
    """Register an Airflow 3 API. The password is stored on the instance row."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    verify_tls: bool = True
    description: str | None = Field(default=None, max_length=16000)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return http_base_url(value)

    @field_validator("username", "password")
    @classmethod
    def _credentials(cls, value: str) -> str:
        return _non_blank(value)


class AirflowInstanceUpdate(BaseModel):
    """Patch an instance. An omitted password keeps the stored one."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    username: str | None = Field(default=None, min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    verify_tls: bool | None = None
    description: str | None = Field(default=None, max_length=16000)
    context: str | None = Field(default=None, max_length=4000)
    status: AirflowInstanceStatus | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return http_base_url(value)

    @field_validator("username", "password")
    @classmethod
    def _credentials(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _non_blank(value)


class AirflowInstanceRead(BaseModel):
    """Instance returned by the API. The password is omitted."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    asset_id: UUID | None = None
    name: str
    slug: str
    base_url: str
    username: str
    verify_tls: bool
    description: str | None
    context: str | None
    context_origin: ContextOrigin | None
    status: AirflowInstanceStatus
    has_credentials: bool
    dag_count: int
    last_synced_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class AirflowConnectionTestRequest(BaseModel):
    """Credentials to try without saving an instance."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    verify_tls: bool = True

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return http_base_url(value)

    @field_validator("username", "password")
    @classmethod
    def _credentials(cls, value: str) -> str:
        return _non_blank(value)


class AirflowHealthView(BaseModel):
    """Component status strings from the Airflow health endpoint."""

    model_config = ConfigDict(extra="forbid")

    metadatabase: str | None
    scheduler: str | None
    triggerer: str | None
    dag_processor: str | None


class AirflowConnectionTest(BaseModel):
    """Result of a connection attempt. Failure stays on this body."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    version: str | None
    health: AirflowHealthView | None
    error: str | None


class AirflowPoolView(BaseModel):
    """Pool summary."""

    model_config = ConfigDict(extra="forbid")

    name: str
    slots: int
    open_slots: int
    description: str | None


class AirflowProviderView(BaseModel):
    """Installed provider summary."""

    model_config = ConfigDict(extra="forbid")

    package_name: str
    version: str


class AirflowPluginView(BaseModel):
    """Loaded plugin summary."""

    model_config = ConfigDict(extra="forbid")

    name: str
    source: str | None


class AirflowImportErrorView(BaseModel):
    """DAG import error. The stack trace is truncated."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    timestamp: datetime | None
    bundle_name: str | None
    stack_trace: str | None


class AirflowInstanceOverview(BaseModel):
    """Live Airflow metadata. A section is null when that call failed."""

    model_config = ConfigDict(extra="forbid")

    version: str | None
    version_error: str | None
    health: AirflowHealthView | None
    health_error: str | None
    pools: list[AirflowPoolView] | None
    pools_error: str | None
    providers: list[AirflowProviderView] | None
    providers_error: str | None
    plugins: list[AirflowPluginView] | None
    plugins_error: str | None
    import_errors: list[AirflowImportErrorView] | None
    import_errors_error: str | None


class AirflowTaskRead(BaseModel):
    """Stored task snapshot."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    operator_name: str | None = None
    downstream_task_ids: list[str] = []


class AirflowLastRunRead(BaseModel):
    """Newest stored DAG run."""

    model_config = ConfigDict(extra="forbid")

    dag_run_id: str
    state: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    run_type: str | None = None


class AirflowRunStatRead(BaseModel):
    """Stored run count for one state."""

    model_config = ConfigDict(extra="forbid")

    state: str
    count: int


class AirflowDagTableRead(BaseModel):
    """DAG to catalog table link for the UI."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    dag_row_id: UUID
    dag_id: str
    catalog_table_id: UUID
    table_name: str
    qualified_name: str
    origin: AirflowMappingOrigin
    direction: AirflowMappingDirection
    evidence: str | None


class AirflowDagRead(BaseModel):
    """Synced DAG without source text."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    instance_id: UUID
    asset_id: UUID | None = None
    dag_id: str
    display_name: str | None
    description: str | None
    is_paused: bool
    is_stale: bool
    has_import_errors: bool
    timetable_summary: str | None
    timetable_description: str | None
    owners: list[str]
    tags: list[str]
    fileloc: str | None
    bundle_name: str | None
    max_active_runs: int | None
    next_dagrun_run_after: datetime | None
    last_parsed_time: datetime | None
    last_run: AirflowLastRunRead | None
    run_stats: list[AirflowRunStatRead]
    tables: list[AirflowDagTableRead]
    last_seen_at: datetime


class AirflowDagDetail(AirflowDagRead):
    """Synced DAG plus tasks and source."""

    tasks: list[AirflowTaskRead]
    source_code: str | None
    source_version: int | None


class AirflowDagRunRead(BaseModel):
    """Live DAG run from Airflow."""

    model_config = ConfigDict(extra="forbid")

    dag_run_id: str
    state: str | None
    run_type: str | None
    start_date: datetime | None
    end_date: datetime | None
    logical_date: datetime | None
    duration: float | None


class AirflowDagSourceRead(BaseModel):
    """Stored DAG source."""

    model_config = ConfigDict(extra="forbid")

    dag_id: str
    content: str | None
    version_number: int | None


class AirflowDagTableCreate(BaseModel):
    """Manual link from a DAG to a catalog table."""

    model_config = ConfigDict(extra="forbid")

    catalog_table_id: UUID
    direction: AirflowMappingDirection = AirflowMappingDirection.UNKNOWN


class AirflowTableDagRead(BaseModel):
    """Reverse lookup: DAGs that reference one catalog table."""

    model_config = ConfigDict(extra="forbid")

    instance_id: UUID
    instance_name: str
    dag_id: str
    origin: AirflowMappingOrigin
    direction: AirflowMappingDirection
    evidence: str | None


AirflowAttachmentTargetKind = Literal["space", "scope"]
AirflowAttachmentVia = Literal["instance", "dag"]


class AirflowAttachmentCreate(BaseModel):
    """Attach a whole instance, or one DAG when dag_id is set."""

    model_config = ConfigDict(extra="forbid")

    instance_id: UUID
    dag_id: str | None = Field(default=None, min_length=1, max_length=250)

    @field_validator("dag_id")
    @classmethod
    def _dag_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _non_blank(value)


class AirflowAttachmentRead(BaseModel):
    """One Airflow attachment on a space or scope."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    target_kind: AirflowAttachmentTargetKind
    target_id: UUID
    instance_id: UUID
    instance_name: str
    instance_slug: str
    dag_row_id: UUID | None
    dag_id: str | None
    dag_display_name: str | None
    created_at: datetime


class AirflowAttachedDagRead(BaseModel):
    """A DAG that belongs to a space or scope through an attachment."""

    model_config = ConfigDict(extra="forbid")

    attachment_id: UUID
    dag_row_id: UUID
    instance_id: UUID
    instance_name: str
    dag_id: str
    display_name: str | None
    description: str | None
    timetable_summary: str | None
    is_paused: bool
    last_run: AirflowLastRunRead | None
    table_count: int
    via: AirflowAttachmentVia
    summary: str | None


class AirflowSyncCounts(BaseModel):
    """Counters stored on a sync run."""

    model_config = ConfigDict(extra="forbid")

    dags_seen: int = 0
    dags_removed: int = 0
    mappings: int = 0
    dag_errors: int = 0


class AirflowSyncRunRead(BaseModel):
    """One finished or failed sync."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    instance_id: UUID
    started_at: datetime
    finished_at: datetime | None
    status: SyncRunStatus
    counts: AirflowSyncCounts
    sanitized_error: str | None
