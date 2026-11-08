"""Asset list, runs, markdown outputs, and a manual trigger."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.api.dependencies import AssetServiceDep
from intelegent_data_catalog.assets.definitions.models import AssetPage, AssetRead, AssetRunQueued
from intelegent_data_catalog.assets.execution.models import (
    AssetOutputDetail,
    AssetOutputRead,
    AssetRunRead,
    AssetRunTaskDetail,
)
from intelegent_data_catalog.assets.profiling.models import AssetProfileRead
from intelegent_data_catalog.assets.service import AssetService
from intelegent_data_catalog.graph.document import GraphView
from intelegent_data_catalog.persistence.enums import AssetKind

router: APIRouter = APIRouter()


class AssetRunRequest(BaseModel):
    """Body for a manual asset run."""

    model_config = ConfigDict(extra="forbid")

    force: bool = False


@router.get("/assets", response_model=AssetPage)
def search_assets(
    service: AssetServiceDep,
    q: Annotated[str | None, Query()] = None,
    kind: Annotated[AssetKind | None, Query()] = None,
    sort: Annotated[Literal["name", "kind", "updated"], Query()] = "name",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AssetPage:
    """Return one page of assets."""
    return service.search(text=q, kind=kind, sort=sort, limit=limit, offset=offset)


@router.get("/assets/{asset_id}", response_model=AssetRead)
def read_asset(asset_id: UUID, service: AssetServiceDep) -> AssetRead:
    """Return one asset."""
    return service.get(asset_id)


@router.post("/assets/{asset_id}/run", response_model=AssetRunQueued, status_code=202)
def run_asset(asset_id: UUID, body: AssetRunRequest, service: AssetServiceDep) -> AssetRunQueued:
    """Queue the asset's operators. ``force`` reruns a GitHub snapshot on the same commit."""
    service.trigger(asset_id, force=body.force)
    return AssetRunQueued(queued=True)


@router.get("/assets/{asset_id}/runs", response_model=list[AssetRunRead])
def list_asset_runs(
    asset_id: UUID,
    service: AssetServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AssetRunRead]:
    """Return recent operator runs."""
    return service.runs(asset_id, limit)


@router.get(
    "/assets/{asset_id}/runs/{run_id}/tasks/{task_id}",
    response_model=AssetRunTaskDetail,
)
def read_asset_run_task(
    asset_id: UUID,
    run_id: UUID,
    task_id: str,
    service: AssetServiceDep,
) -> AssetRunTaskDetail:
    """Return one task log."""
    found: AssetService = service
    return found.run_task(asset_id, run_id, task_id)


@router.get("/assets/{asset_id}/profile", response_model=AssetProfileRead)
def read_asset_profile(asset_id: UUID, service: AssetServiceDep) -> AssetProfileRead:
    """Return the newest data profile for one asset."""
    return service.profile(asset_id)


@router.get("/assets/{asset_id}/profile/graph", response_model=GraphView)
def read_asset_profile_graph(asset_id: UUID, service: AssetServiceDep) -> GraphView:
    """Return the Neo4j profile subgraph for one asset."""
    return service.profile_graph(asset_id)


@router.get("/assets/{asset_id}/outputs", response_model=list[AssetOutputRead])
def list_asset_outputs(asset_id: UUID, service: AssetServiceDep) -> list[AssetOutputRead]:
    """Return stored markdown results, newest first."""
    return service.outputs(asset_id)


@router.get("/assets/{asset_id}/outputs/{output_id}", response_model=AssetOutputDetail)
def read_asset_output(
    asset_id: UUID,
    output_id: UUID,
    service: AssetServiceDep,
) -> AssetOutputDetail:
    """Return one markdown document and its JSON sidecar."""
    return service.output(asset_id, output_id)
