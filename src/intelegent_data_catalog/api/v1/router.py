"""Aggregates version 1 routes under /api/v1."""

from fastapi import APIRouter

from intelegent_data_catalog.api.v1.routes import catalog, health, query

router: APIRouter = APIRouter(prefix="/api/v1")
router.include_router(health.router, tags=["health"])
router.include_router(catalog.router, tags=["catalog"])
router.include_router(query.router, tags=["query"])
