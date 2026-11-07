"""Aggregates version 1 routes under /api/v1."""

from fastapi import APIRouter, Depends

from intelegent_data_catalog.api.dependencies import (
    get_access,
    require_auth,
)
from intelegent_data_catalog.api.v1.routes import (
    agent_definitions,
    agents,
    airflow,
    asset_definitions,
    assets,
    catalog,
    configuration,
    health,
    notebooks,
    platform,
    query,
    registry,
    schedules,
    workspace_files,
    workspaces,
)

_signed_in = [Depends(get_access)]
_authenticated = [Depends(require_auth)]

router: APIRouter = APIRouter(prefix="/api/v1")
router.include_router(health.router, tags=["health"])
router.include_router(platform.router, tags=["platform"], dependencies=_signed_in)
router.include_router(airflow.router, tags=["airflow"], dependencies=_signed_in)
router.include_router(schedules.router, tags=["schedules"], dependencies=_signed_in)
router.include_router(configuration.router, tags=["configuration"], dependencies=_signed_in)
router.include_router(registry.router, tags=["registry"], dependencies=_signed_in)
router.include_router(agents.router, tags=["agents"], dependencies=_signed_in)
router.include_router(
    agent_definitions.router,
    tags=["agent-definitions"],
    dependencies=_signed_in,
)
router.include_router(assets.router, tags=["assets"], dependencies=_signed_in)
router.include_router(
    asset_definitions.router,
    tags=["asset-definitions"],
    dependencies=_signed_in,
)
router.include_router(workspaces.router, tags=["workspaces"], dependencies=_signed_in)
router.include_router(workspace_files.router, tags=["workspace-files"], dependencies=_signed_in)
router.include_router(notebooks.router, tags=["notebooks"])
router.include_router(catalog.router, tags=["catalog"], dependencies=_authenticated)
router.include_router(query.router, tags=["query"], dependencies=_authenticated)
