"""Airflow instance, DAG snapshot, and table mapping routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from intelegent_data_catalog.airflow.schemas import (
    AirflowConnectionTest,
    AirflowConnectionTestRequest,
    AirflowDagDetail,
    AirflowDagRead,
    AirflowDagRunRead,
    AirflowDagSourceRead,
    AirflowDagTableCreate,
    AirflowDagTableRead,
    AirflowInstanceCreate,
    AirflowInstanceOverview,
    AirflowInstanceRead,
    AirflowInstanceUpdate,
    AirflowSyncRunRead,
    AirflowTableDagRead,
)
from intelegent_data_catalog.api.dependencies import AirflowServiceDep

router: APIRouter = APIRouter()


@router.get("/airflow/instances", response_model=list[AirflowInstanceRead])
def list_airflow_instances(service: AirflowServiceDep) -> list[AirflowInstanceRead]:
    """List registered Airflow instances."""
    return service.list_instances()


@router.post("/airflow/instances", response_model=AirflowInstanceRead, status_code=201)
def create_airflow_instance(
    body: AirflowInstanceCreate,
    service: AirflowServiceDep,
) -> AirflowInstanceRead:
    """Register an Airflow 3 instance."""
    return service.create_instance(body)


@router.post("/airflow/instances/test", response_model=AirflowConnectionTest)
def test_airflow_instance(
    body: AirflowConnectionTestRequest,
    service: AirflowServiceDep,
) -> AirflowConnectionTest:
    """Try an Airflow URL and password without saving them."""
    return service.test_connection(body)


@router.get("/airflow/instances/{instance_id}", response_model=AirflowInstanceRead)
def read_airflow_instance(instance_id: UUID, service: AirflowServiceDep) -> AirflowInstanceRead:
    """Return one Airflow instance."""
    return service.get_instance(instance_id)


@router.patch("/airflow/instances/{instance_id}", response_model=AirflowInstanceRead)
def patch_airflow_instance(
    instance_id: UUID,
    body: AirflowInstanceUpdate,
    service: AirflowServiceDep,
) -> AirflowInstanceRead:
    """Patch an Airflow instance. An omitted password is kept."""
    return service.update_instance(instance_id, body)


@router.delete("/airflow/instances/{instance_id}", status_code=204)
def remove_airflow_instance(instance_id: UUID, service: AirflowServiceDep) -> Response:
    """Delete an Airflow instance and its snapshots."""
    service.delete_instance(instance_id)
    return Response(status_code=204)


@router.post("/airflow/instances/{instance_id}/sync", response_model=AirflowSyncRunRead)
def sync_airflow_instance(instance_id: UUID, service: AirflowServiceDep) -> AirflowSyncRunRead:
    """Sync DAGs, runs, source, and discovered table links."""
    return service.sync(instance_id)


@router.get(
    "/airflow/instances/{instance_id}/sync-runs",
    response_model=list[AirflowSyncRunRead],
)
def list_airflow_sync_runs(
    instance_id: UUID,
    service: AirflowServiceDep,
) -> list[AirflowSyncRunRead]:
    """List recorded syncs."""
    return service.list_sync_runs(instance_id)


@router.get(
    "/airflow/instances/{instance_id}/overview",
    response_model=AirflowInstanceOverview,
)
def read_airflow_overview(
    instance_id: UUID,
    service: AirflowServiceDep,
) -> AirflowInstanceOverview:
    """Read live version, health, pools, providers, plugins, and import errors."""
    return service.overview(instance_id)


@router.get("/airflow/instances/{instance_id}/dags", response_model=list[AirflowDagRead])
def list_airflow_dags(instance_id: UUID, service: AirflowServiceDep) -> list[AirflowDagRead]:
    """List the synced DAG snapshot."""
    return service.list_dags(instance_id)


@router.get(
    "/airflow/instances/{instance_id}/dags/{dag_id}",
    response_model=AirflowDagDetail,
)
def read_airflow_dag(
    instance_id: UUID,
    dag_id: str,
    service: AirflowServiceDep,
) -> AirflowDagDetail:
    """Return one synced DAG."""
    return service.get_dag(instance_id, dag_id)


@router.get(
    "/airflow/instances/{instance_id}/dags/{dag_id}/runs",
    response_model=list[AirflowDagRunRead],
)
def list_airflow_dag_runs(
    instance_id: UUID,
    dag_id: str,
    service: AirflowServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[AirflowDagRunRead]:
    """Read recent DAG runs live from Airflow."""
    return service.list_dag_runs(instance_id, dag_id, limit)


@router.get(
    "/airflow/instances/{instance_id}/dags/{dag_id}/source",
    response_model=AirflowDagSourceRead,
)
def read_airflow_dag_source(
    instance_id: UUID,
    dag_id: str,
    service: AirflowServiceDep,
) -> AirflowDagSourceRead:
    """Return the DAG source stored by the last sync."""
    return service.dag_source(instance_id, dag_id)


@router.post(
    "/airflow/instances/{instance_id}/dags/{dag_id}/tables",
    response_model=AirflowDagTableRead,
    status_code=201,
)
def add_airflow_dag_table(
    instance_id: UUID,
    dag_id: str,
    body: AirflowDagTableCreate,
    service: AirflowServiceDep,
) -> AirflowDagTableRead:
    """Link a catalog table to a DAG by hand."""
    return service.add_table_mapping(instance_id, dag_id, body)


@router.delete(
    "/airflow/instances/{instance_id}/dags/{dag_id}/tables/{table_id}",
    status_code=204,
)
def remove_airflow_dag_table(
    instance_id: UUID,
    dag_id: str,
    table_id: UUID,
    service: AirflowServiceDep,
) -> Response:
    """Remove a manual DAG-to-table link."""
    service.remove_table_mapping(instance_id, dag_id, table_id)
    return Response(status_code=204)


















@router.get("/tables/{table_id}/airflow-dags", response_model=list[AirflowTableDagRead])
def list_table_airflow_dags(
    table_id: UUID,
    service: AirflowServiceDep,
) -> list[AirflowTableDagRead]:
    """Return DAGs linked to one catalog table."""
    return service.dags_for_table(table_id)
