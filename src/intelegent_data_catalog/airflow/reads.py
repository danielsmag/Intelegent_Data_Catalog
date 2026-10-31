"""Map Airflow rows to API models."""

from datetime import UTC, datetime
from typing import cast

from pydantic import ValidationError

from intelegent_data_catalog.airflow.schemas import (
    AirflowDagDetail,
    AirflowDagRead,
    AirflowDagTableRead,
    AirflowInstanceRead,
    AirflowLastRunRead,
    AirflowRunStatRead,
    AirflowSyncCounts,
    AirflowSyncRunRead,
    AirflowTableDagRead,
    AirflowTaskRead,
)
from intelegent_data_catalog.persistence.enums import (
    AirflowInstanceStatus,
    AirflowMappingDirection,
    AirflowMappingOrigin,
    ContextOrigin,
    SyncRunStatus,
)
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AirflowDagTableRow,
    AirflowInstanceRow,
    AirflowSyncRunRow,
    CatalogTableRow,
)


def instance_read(row: AirflowInstanceRow, dag_count: int) -> AirflowInstanceRead:
    """Build an instance response. The password stays on the row."""
    return AirflowInstanceRead(
        id=row.id,
        asset_id=row.asset_id,
        name=row.name,
        slug=row.slug,
        base_url=row.base_url,
        username=row.username,
        verify_tls=row.verify_tls,
        description=row.description,
        context=row.context,
        context_origin=_context_origin(row.context_origin),
        status=AirflowInstanceStatus(row.status),
        has_credentials=row.password.strip() != "",
        dag_count=dag_count,
        last_synced_at=_aware_optional(row.last_synced_at),
        last_error=row.last_error,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def dag_read(row: AirflowDagRow, tables: list[AirflowDagTableRead]) -> AirflowDagRead:
    """Build a DAG list item."""
    return AirflowDagRead(
        id=row.id,
        instance_id=row.instance_id,
        asset_id=row.asset_id,
        dag_id=row.dag_id,
        display_name=row.display_name,
        description=row.description,
        is_paused=row.is_paused,
        is_stale=row.is_stale,
        has_import_errors=row.has_import_errors,
        timetable_summary=row.timetable_summary,
        timetable_description=row.timetable_description,
        owners=_strings(row.owners),
        tags=_strings(row.tags),
        fileloc=row.fileloc,
        bundle_name=row.bundle_name,
        max_active_runs=row.max_active_runs,
        next_dagrun_run_after=_aware_optional(row.next_dagrun_run_after),
        last_parsed_time=_aware_optional(row.last_parsed_time),
        last_run=_last_run(row.last_run),
        run_stats=_run_stats(row.run_stats),
        tables=tables,
        last_seen_at=_aware(row.last_seen_at),
    )


def dag_detail(row: AirflowDagRow, tables: list[AirflowDagTableRead]) -> AirflowDagDetail:
    """Build a DAG detail response, including stored source."""
    base: AirflowDagRead = dag_read(row, tables)
    return AirflowDagDetail(
        **base.model_dump(),
        tasks=_tasks(row.tasks),
        source_code=row.source_code,
        source_version=row.source_version,
    )


def mapping_read(
    row: AirflowDagTableRow,
    dag: AirflowDagRow,
    table: CatalogTableRow,
) -> AirflowDagTableRead:
    """Build one DAG-to-table link."""
    qualified: str = f"{table.physical_catalog}.{table.physical_namespace}.{table.physical_table}"
    name: str = table.display_name if table.display_name is not None else table.name
    return AirflowDagTableRead(
        id=row.id,
        dag_row_id=row.dag_row_id,
        dag_id=dag.dag_id,
        catalog_table_id=table.id,
        table_name=name,
        qualified_name=qualified,
        origin=AirflowMappingOrigin(row.origin),
        direction=AirflowMappingDirection(row.direction),
        evidence=row.evidence,
    )


def table_dag_read(
    row: AirflowDagTableRow,
    dag: AirflowDagRow,
    instance: AirflowInstanceRow,
) -> AirflowTableDagRead:
    """Build a reverse link from a catalog table to a DAG."""
    return AirflowTableDagRead(
        instance_id=instance.id,
        instance_name=instance.name,
        dag_id=dag.dag_id,
        origin=AirflowMappingOrigin(row.origin),
        direction=AirflowMappingDirection(row.direction),
        evidence=row.evidence,
    )


def sync_run_read(row: AirflowSyncRunRow) -> AirflowSyncRunRead:
    """Build a sync run response."""
    return AirflowSyncRunRead(
        id=row.id,
        instance_id=row.instance_id,
        started_at=_aware(row.started_at),
        finished_at=_aware_optional(row.finished_at),
        status=SyncRunStatus(row.status),
        counts=_counts(row.counts),
        sanitized_error=row.sanitized_error,
    )


def _tasks(value: object) -> list[AirflowTaskRead]:
    return _models(value, AirflowTaskRead)


def _run_stats(value: object) -> list[AirflowRunStatRead]:
    return _models(value, AirflowRunStatRead)


def _last_run(value: object) -> AirflowLastRunRead | None:
    if not isinstance(value, dict):
        return None
    try:
        return AirflowLastRunRead.model_validate(value)
    except ValidationError:
        return None


def _models[ModelT: AirflowTaskRead | AirflowRunStatRead](
    value: object,
    model: type[ModelT],
) -> list[ModelT]:
    if not isinstance(value, list):
        return []
    found: list[ModelT] = []
    item: object
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            parsed: ModelT = cast(ModelT, model.model_validate(item))
            found.append(parsed)
        except ValidationError:
            continue
    return found


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    found: list[str] = []
    item: object
    for item in value:
        if isinstance(item, str):
            found.append(item)
    return found


def _counts(value: object) -> AirflowSyncCounts:
    if not isinstance(value, dict):
        return AirflowSyncCounts()
    try:
        return AirflowSyncCounts.model_validate(value)
    except ValidationError:
        return AirflowSyncCounts()


def _context_origin(value: str | None) -> ContextOrigin | None:
    if value is None:
        return None
    return ContextOrigin(value)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _aware_optional(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _aware(value)
