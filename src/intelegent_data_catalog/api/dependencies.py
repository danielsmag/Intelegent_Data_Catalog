"""FastAPI dependencies that resolve services from the application container."""

from collections.abc import Iterator
from typing import Annotated, cast

from dependency_injector.containers import DynamicContainer
from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.catalog.services.catalog_service import CatalogService
from intelegent_data_catalog.catalog.services.explorer_service import ExplorerService
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.catalog.services.scope_attachment import ScopeAttachmentService
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.exceptions import PersistenceUnavailableError
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.registry.service import RegistryService


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


def get_explorer_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> ExplorerService:
    """Return the multi-source catalog browser."""
    service: ExplorerService = container.explorer_service()
    return service


def get_registry_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[RegistryService]:
    """Open a catalog session for one request."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    service: RegistryService = RegistryService(session)
    try:
        yield service
    except SQLAlchemyError as err:
        session.rollback()
        raise PersistenceUnavailableError("catalog database unavailable") from err
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]
QueryServiceDep = Annotated[QueryService, Depends(get_query_service)]
ExplorerServiceDep = Annotated[ExplorerService, Depends(get_explorer_service)]
RegistryServiceDep = Annotated[RegistryService, Depends(get_registry_service)]


def get_scope_attachment(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[ScopeAttachmentService]:
    """Open one catalog session and attach source tables into a scope."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    explorer: ExplorerService = container.explorer_service()
    service: ScopeAttachmentService = ScopeAttachmentService(RegistryService(session), explorer)
    try:
        yield service
    except SQLAlchemyError as err:
        session.rollback()
        raise PersistenceUnavailableError("catalog database unavailable") from err
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


ScopeAttachmentDep = Annotated[ScopeAttachmentService, Depends(get_scope_attachment)]


def get_platform(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[RequestPlatform]:
    """Open one catalog session and the discovery, graph, and query services."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    settings: AppSettings = container.settings()
    query: QueryService = container.query_service()
    platform: RequestPlatform = RequestPlatform(session, settings, query)
    try:
        yield platform
    except SQLAlchemyError as err:
        session.rollback()
        raise PersistenceUnavailableError("catalog database unavailable") from err
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


PlatformDep = Annotated[RequestPlatform, Depends(get_platform)]
