"""Composition root for the catalog API."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from intelegent_data_catalog.api.app import configure_app
from intelegent_data_catalog.core.container import AppContainer


def create_app(container: AppContainer | None = None) -> FastAPI:
    """Build the catalog API. A container argument is used by tests."""
    app_container: AppContainer = AppContainer() if container is None else container
    app: FastAPI = FastAPI(
        title="Intelegent Data Catalog",
        lifespan=_lifespan(app_container),
    )
    app.state.container = app_container
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
