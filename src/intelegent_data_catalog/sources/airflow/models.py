"""Airflow 3 REST payloads. Extra fields are ignored so minor releases stay readable."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


def _as_int(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value)
    return value


def _dag_refs(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    refs: list[object] = []
    item: object
    for item in value:
        if isinstance(item, str):
            refs.append({"dag_id": item})
        else:
            refs.append(item)
    return refs


class AirflowVersion(BaseModel):
    """GET /api/v2/version."""

    model_config = ConfigDict(extra="ignore")

    version: str


class AirflowHealthComponent(BaseModel):
    """One process reported by the monitor health endpoint."""

    model_config = ConfigDict(extra="ignore")

    status: str | None = None


class AirflowHealth(BaseModel):
    """GET /api/v2/monitor/health."""

    model_config = ConfigDict(extra="ignore")

    metadatabase: AirflowHealthComponent
    scheduler: AirflowHealthComponent
    triggerer: AirflowHealthComponent | None = None
    dag_processor: AirflowHealthComponent | None = None


class AirflowTag(BaseModel):
    """Tag object on a DAG collection entry."""

    model_config = ConfigDict(extra="ignore")

    name: str


class AirflowDag(BaseModel):
    """One DAG from GET /api/v2/dags."""

    model_config = ConfigDict(extra="ignore")

    dag_id: str
    dag_display_name: str | None = None
    is_paused: bool = False
    is_stale: bool = False
    last_parsed_time: datetime | None = None
    fileloc: str | None = None
    description: str | None = None
    timetable_summary: str | None = None
    timetable_description: str | None = None
    tags: list[AirflowTag] = []
    max_active_runs: int | None = None
    has_import_errors: bool = False
    next_dagrun_run_after: datetime | None = None
    owners: list[str] = []
    bundle_name: str | None = None

    @field_validator("max_active_runs", mode="before")
    @classmethod
    def _max_active_runs(cls, value: object) -> object:
        return _as_int(value)


class AirflowTask(BaseModel):
    """One task from GET /api/v2/dags/{dag_id}/tasks."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    operator_name: str | None = None
    downstream_task_ids: list[str] = []


class AirflowDagRun(BaseModel):
    """One DAG run from GET /api/v2/dags/{dag_id}/dagRuns."""

    model_config = ConfigDict(extra="ignore")

    dag_run_id: str
    state: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    logical_date: datetime | None = None
    run_type: str | None = None
    duration: float | None = None


class AirflowDagSource(BaseModel):
    """GET /api/v2/dagSources/{dag_id}."""

    model_config = ConfigDict(extra="ignore")

    dag_id: str
    content: str | None = None
    version_number: int | None = None

    @field_validator("version_number", mode="before")
    @classmethod
    def _version_number(cls, value: object) -> object:
        return _as_int(value)


class AirflowRunStat(BaseModel):
    """Run count for one state."""

    model_config = ConfigDict(extra="ignore")

    state: str
    count: int

    @field_validator("count", mode="before")
    @classmethod
    def _count(cls, value: object) -> object:
        return _as_int(value)


class AirflowDagStats(BaseModel):
    """GET /api/v2/dagStats entry."""

    model_config = ConfigDict(extra="ignore")

    dag_id: str
    stats: list[AirflowRunStat] = []


class AssetTaskRef(BaseModel):
    """Task that produces or consumes an asset."""

    model_config = ConfigDict(extra="ignore")

    dag_id: str | None = None
    task_id: str | None = None


class AssetDagRef(BaseModel):
    """DAG scheduled or listed against an asset."""

    model_config = ConfigDict(extra="ignore")

    dag_id: str | None = None


class AirflowAsset(BaseModel):
    """GET /api/v2/assets entry. Inlet tasks are treated as consuming DAGs."""

    model_config = ConfigDict(extra="ignore")

    uri: str
    name: str | None = None
    producing_tasks: list[AssetTaskRef] = []
    consuming_tasks: list[AssetTaskRef] = []
    consuming_dags: list[AssetDagRef] = []
    scheduled_dags: list[AssetDagRef] = []

    @field_validator("consuming_dags", "scheduled_dags", mode="before")
    @classmethod
    def _dag_lists(cls, value: object) -> list[object]:
        return _dag_refs(value)


class AirflowPool(BaseModel):
    """GET /api/v2/pools entry."""

    model_config = ConfigDict(extra="ignore")

    name: str
    slots: int
    open_slots: int
    description: str | None = None

    @field_validator("slots", "open_slots", mode="before")
    @classmethod
    def _slots(cls, value: object) -> object:
        return _as_int(value)


class AirflowProvider(BaseModel):
    """GET /api/v2/providers entry."""

    model_config = ConfigDict(extra="ignore")

    package_name: str
    version: str
    description: str | None = None


class AirflowPlugin(BaseModel):
    """GET /api/v2/plugins entry."""

    model_config = ConfigDict(extra="ignore")

    name: str
    source: str | None = None


class AirflowImportError(BaseModel):
    """GET /api/v2/importErrors entry."""

    model_config = ConfigDict(extra="ignore")

    filename: str
    timestamp: datetime | None = None
    bundle_name: str | None = None
    stack_trace: str | None = None
