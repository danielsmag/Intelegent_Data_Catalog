"""Per-target sync schedule routes."""

from uuid import UUID

from fastapi import APIRouter, Response

from intelegent_data_catalog.api.dependencies import SyncScheduleDep
from intelegent_data_catalog.jobs.schemas import SyncScheduleRead, SyncScheduleWrite

router: APIRouter = APIRouter()


@router.get(
    "/source-bindings/{source_binding_id}/schedule",
    response_model=SyncScheduleRead,
)
def read_binding_schedule(
    source_binding_id: UUID,
    service: SyncScheduleDep,
) -> SyncScheduleRead:
    """Return the sync interval for one Trino source binding."""
    return service.get_binding(source_binding_id)


@router.put(
    "/source-bindings/{source_binding_id}/schedule",
    response_model=SyncScheduleRead,
)
def put_binding_schedule(
    source_binding_id: UUID,
    body: SyncScheduleWrite,
    service: SyncScheduleDep,
) -> SyncScheduleRead:
    """Create or replace the sync interval for one Trino source binding."""
    return service.put_binding(source_binding_id, body)


@router.delete("/source-bindings/{source_binding_id}/schedule", status_code=204)
def delete_binding_schedule(source_binding_id: UUID, service: SyncScheduleDep) -> Response:
    """Remove the sync interval for one Trino source binding."""
    service.delete_binding(source_binding_id)
    return Response(status_code=204)


@router.get(
    "/airflow/instances/{instance_id}/schedule",
    response_model=SyncScheduleRead,
)
def read_airflow_schedule(instance_id: UUID, service: SyncScheduleDep) -> SyncScheduleRead:
    """Return the sync interval for one Airflow instance."""
    return service.get_airflow(instance_id)


@router.put(
    "/airflow/instances/{instance_id}/schedule",
    response_model=SyncScheduleRead,
)
def put_airflow_schedule(
    instance_id: UUID,
    body: SyncScheduleWrite,
    service: SyncScheduleDep,
) -> SyncScheduleRead:
    """Create or replace the sync interval for one Airflow instance."""
    return service.put_airflow(instance_id, body)


@router.delete("/airflow/instances/{instance_id}/schedule", status_code=204)
def delete_airflow_schedule(instance_id: UUID, service: SyncScheduleDep) -> Response:
    """Remove the sync interval for one Airflow instance."""
    service.delete_airflow(instance_id)
    return Response(status_code=204)
