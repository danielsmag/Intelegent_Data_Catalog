"""Composition root for the catalog API."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

import intelegent_data_catalog.jobs.celery_app as catalog_celery
from intelegent_data_catalog.api.app import configure_app
from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs
from intelegent_data_catalog.jobs.publisher import CeleryJobPublisher
from intelegent_data_catalog.jobs.runtime import set_job_container


def create_app(container: AppContainer | None = None) -> FastAPI:
    """Build the catalog API. A container argument is used by tests."""
    app_container: AppContainer = AppContainer() if container is None else container
    app: FastAPI = FastAPI(
        title="Intelegent Data Catalog",
        lifespan=_lifespan(app_container),
    )
    app.state.container = app_container
    app.state.celery_app = catalog_celery.celery_app
    set_job_container(app_container)
    app.state.catalog_jobs = CatalogJobs(app_container.database(), CeleryJobPublisher())
    configure_app(app)
    return app


def _lifespan(
    container: AppContainer,
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
