"""FastAPI application for the catalog explorer."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

from intelegent_data_catalog.api.container import ApiContainer
from intelegent_data_catalog.catalog.models import CatalogTree, TableDetail
from intelegent_data_catalog.catalog.service import CatalogService
from intelegent_data_catalog.exceptions import PolarisError, TableNotFoundError

DEV_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class ErrorResponse(BaseModel):
    """JSON error body."""

    model_config = ConfigDict(extra="forbid")

    detail: str


class HealthResponse(BaseModel):
    """Process health, without calling Polaris."""

    model_config = ConfigDict(extra="forbid")

    status: str


def create_app(container: ApiContainer | None = None) -> FastAPI:
    """Build the catalog API. A container argument is used by tests."""
    app_container: ApiContainer = ApiContainer() if container is None else container
    app: FastAPI = FastAPI(
        title="Intelegent Data Catalog",
        lifespan=_lifespan(app_container),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.add_exception_handler(TableNotFoundError, _table_not_found)
    app.add_exception_handler(PolarisError, _polaris_failed)

    @app.get("/api/health", response_model=HealthResponse)
    def read_health() -> HealthResponse:
        """Report that the API process is up."""
        return HealthResponse(status="ok")

    @app.get("/api/tree", response_model=CatalogTree)
    def read_tree() -> CatalogTree:
        """Return catalogs, schemas, and tables."""
        service: CatalogService = app_container.catalog_service()
        return service.tree()

    @app.get("/api/tables/{catalog}/{namespace}/{table}", response_model=TableDetail)
    def read_table(catalog: str, namespace: str, table: str) -> TableDetail:
        """Return metadata for one table."""
        service: CatalogService = app_container.catalog_service()
        return service.table(catalog, namespace, table)

    return app


def _lifespan(
    container: ApiContainer,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        container.logger()
        container.init_resources()
        try:
            yield
        finally:
            container.shutdown_resources()

    return lifespan


def _table_not_found(_request: Request, err: Exception) -> JSONResponse:
    body: ErrorResponse = ErrorResponse(detail=str(err))
    return JSONResponse(status_code=404, content=body.model_dump())


def _polaris_failed(_request: Request, err: Exception) -> JSONResponse:
    body: ErrorResponse = ErrorResponse(detail=str(err))
    return JSONResponse(status_code=502, content=body.model_dump())
