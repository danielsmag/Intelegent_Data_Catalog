"""Aggregates version 1 routes under /api/v1."""

from fastapi import APIRouter

from intelegent_data_catalog.api.v1.routes import (
    airflow,
    catalog,
    configuration,
    health,
    platform,
    query,
    registry,
    schedules,
)

router: APIRouter = APIRouter(prefix="/api/v1")
router.include_router(health.router, tags=["health"])
router.include_router(platform.router, tags=["platform"])
router.include_router(airflow.router, tags=["airflow"])
router.include_router(schedules.router, tags=["schedules"])
router.include_router(configuration.router, tags=["configuration"])
router.include_router(registry.router, tags=["registry"])
router.include_router(catalog.router, tags=["catalog"])
router.include_router(query.router, tags=["query"])
