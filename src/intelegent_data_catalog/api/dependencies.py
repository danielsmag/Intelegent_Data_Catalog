"""FastAPI dependencies that resolve services from the application container."""

from typing import Annotated, cast

from dependency_injector.containers import DynamicContainer
from fastapi import Depends, Request

from intelegent_data_catalog.catalog.services.catalog_service import CatalogService
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.core.container import AppContainer


def get_container(request: Request) -> AppContainer:
    """Return the container stored on the application at startup."""
    container: object = getattr(request.app.state, "container", None)
    if not isinstance(container, DynamicContainer):
        raise RuntimeError("application container is not configured")
    return cast(AppContainer, container)


def get_catalog_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> CatalogService:
    """Return the catalog service."""
    service: CatalogService = container.catalog_service()
    return service


def get_query_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> QueryService:
    """Return the query service."""
    service: QueryService = container.query_service()
    return service


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]
QueryServiceDep = Annotated[QueryService, Depends(get_query_service)]
