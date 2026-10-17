"""Aggregates version 1 routes under /api/v1."""

from fastapi import APIRouter, Depends

from intelegent_data_catalog.api.dependencies import (
    enforce_request_visibility,
    get_access,
    require_auth,
)
from intelegent_data_catalog.api.v1.routes import (
    airflow,
    catalog,
    configuration,
    health,
    platform,
    query,
    registry,
    schedules,
    workspace_files,
    workspaces,
)

_guarded = [Depends(enforce_request_visibility)]
_signed_in = [Depends(get_access)]
_authenticated = [Depends(require_auth)]

router: APIRouter = APIRouter(prefix="/api/v1")
router.include_router(health.router, tags=["health"])
router.include_router(platform.router, tags=["platform"], dependencies=_guarded)
router.include_router(airflow.router, tags=["airflow"], dependencies=_guarded)
router.include_router(schedules.router, tags=["schedules"], dependencies=_guarded)
router.include_router(configuration.router, tags=["configuration"], dependencies=_guarded)
router.include_router(registry.router, tags=["registry"], dependencies=_guarded)
router.include_router(workspaces.router, tags=["workspaces"], dependencies=_signed_in)
router.include_router(workspace_files.router, tags=["workspace-files"], dependencies=_signed_in)
router.include_router(catalog.router, tags=["catalog"], dependencies=_authenticated)
router.include_router(query.router, tags=["query"], dependencies=_authenticated)
