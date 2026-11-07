"""FastAPI dependencies that resolve services from the application container."""

from collections.abc import Iterator
from typing import Annotated, cast

from dependency_injector.containers import DynamicContainer
from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.agent_pack.service import AgentPackService
from intelegent_data_catalog.agents.definitions.listing import AgentDefinitionListing
from intelegent_data_catalog.agents.definitions.service import AgentDefinitionService
from intelegent_data_catalog.agents.service import AgentService
from intelegent_data_catalog.agents.triggers import AgentChangeTrigger
from intelegent_data_catalog.airflow.service import AirflowService
from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.auth.service import AccessService
from intelegent_data_catalog.catalog.services.catalog_service import CatalogService
from intelegent_data_catalog.catalog.services.explorer_service import ExplorerService
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.entity_configuration import EntityConfigurationService
from intelegent_data_catalog.exceptions import AuthenticationError, PersistenceUnavailableError
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs
from intelegent_data_catalog.notebooks.service import NotebookKernelService
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.registry.files import WorkspaceFileService
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.workspaces import WorkspaceService
from intelegent_data_catalog.schedual.schedules import SyncScheduleService
from intelegent_data_catalog.services.bindings import BindingService
from intelegent_data_catalog.services.data_sources import DataSourceService
from intelegent_data_catalog.services.engines import EngineService
from intelegent_data_catalog.services.relationships import RelationshipService
from intelegent_data_catalog.services.sync_runs import SyncRunService
from intelegent_data_catalog.services.tables import TableService


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


def get_catalog_session(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[Session]:
    """Open one catalog session for the request."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    try:
        yield session
    except SQLAlchemyError as err:
        session.rollback()
        raise PersistenceUnavailableError("catalog database unavailable") from err
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


CatalogSessionDep = Annotated[Session, Depends(get_catalog_session)]


def get_data_source_service(session: CatalogSessionDep) -> DataSourceService:
    """Return the data source service for this request."""
    return DataSourceService(RegistryRepository(session))


def get_table_service(session: CatalogSessionDep) -> TableService:
    """Return the table service for this request."""
    return TableService(RegistryRepository(session))


def get_relationship_service(session: CatalogSessionDep) -> RelationshipService:
    """Return the relationship service for this request."""
    return RelationshipService(RegistryRepository(session))


def get_engine_service(session: CatalogSessionDep) -> EngineService:
    """Return the engine connection service for this request."""
    return EngineService(RegistryRepository(session))


def get_binding_service(session: CatalogSessionDep) -> BindingService:
    """Return the binding service for this request."""
    return BindingService(RegistryRepository(session))


def get_sync_run_service(session: CatalogSessionDep) -> SyncRunService:
    """Return the sync run service for this request."""
    return SyncRunService(RegistryRepository(session))


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]
QueryServiceDep = Annotated[QueryService, Depends(get_query_service)]
ExplorerServiceDep = Annotated[ExplorerService, Depends(get_explorer_service)]
DataSourceServiceDep = Annotated[DataSourceService, Depends(get_data_source_service)]
TableServiceDep = Annotated[TableService, Depends(get_table_service)]
RelationshipServiceDep = Annotated[RelationshipService, Depends(get_relationship_service)]
EngineServiceDep = Annotated[EngineService, Depends(get_engine_service)]
BindingServiceDep = Annotated[BindingService, Depends(get_binding_service)]
SyncRunServiceDep = Annotated[SyncRunService, Depends(get_sync_run_service)]


def get_airflow_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[AirflowService]:
    """Open one catalog session for Airflow instance reads and syncs."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    service: AirflowService = AirflowService(
        session,
        container.http_client(),
        container.settings().airflow,
        container.logger().getChild("airflow"),
    )
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


AirflowServiceDep = Annotated[AirflowService, Depends(get_airflow_service)]


def get_platform(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[RequestPlatform]:
    """Open one catalog session and the discovery, graph, and query services."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    settings: AppSettings = container.settings()
    query: QueryService = container.query_service()
    platform: RequestPlatform = RequestPlatform(
        session,
        settings,
        query,
        container.object_store(),
        container.agent_definition_store(),
    )
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


def get_catalog_jobs(request: Request) -> CatalogJobs:
    """Return the Postgres-backed starter created with the application."""
    jobs: object = getattr(request.app.state, "catalog_jobs", None)
    if not isinstance(jobs, CatalogJobs):
        raise RuntimeError("catalog jobs are not configured")
    return jobs


CatalogJobsDep = Annotated[CatalogJobs, Depends(get_catalog_jobs)]


def get_sync_schedules(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[SyncScheduleService]:
    """Open one catalog session for sync schedule reads and writes."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    service: SyncScheduleService = SyncScheduleService(
        session,
        container.settings().llm.default_model_id(),
        container.settings().llm.effort,
    )
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


SyncScheduleDep = Annotated[SyncScheduleService, Depends(get_sync_schedules)]


def get_entity_configuration(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[EntityConfigurationService]:
    """Open one catalog session for entity configuration reads and writes."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    service: EntityConfigurationService = EntityConfigurationService(
        session,
        container.settings().llm.default_model_id(),
        container.settings().llm.effort,
    )
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


EntityConfigurationDep = Annotated[EntityConfigurationService, Depends(get_entity_configuration)]


def get_access(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
) -> AccessContext:
    """Resolve the caller and the active workspace. Auth off uses the dev principal."""
    session: Session = container.database().session()
    try:
        if container.settings().auth.enabled:
            principal: Principal = container.oidc_verifier().verify(_bearer(request))
        else:
            principal = Principal.dev(container.settings().auth)
        return AccessService(session).resolve(principal, request.headers.get("x-workspace-id"))
    except SQLAlchemyError as err:
        session.rollback()
        raise PersistenceUnavailableError("catalog database unavailable") from err
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


AccessDep = Annotated[AccessContext, Depends(get_access)]


def get_workspace_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[WorkspaceService]:
    """Open one catalog session for workspace administration."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    service: WorkspaceService = WorkspaceService(session, container.object_store())
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


WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]


def get_workspace_files(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[WorkspaceFileService]:
    """Open one catalog session for workspace files."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    jobs: object = getattr(request.app.state, "catalog_jobs", None)
    changes: AgentChangeTrigger | None = None
    if isinstance(jobs, CatalogJobs):
        changes = AgentChangeTrigger(session, jobs)
    service: WorkspaceFileService = WorkspaceFileService(
        session,
        container.object_store(),
        container.settings().object_store.max_upload_bytes,
        changes,
    )
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


WorkspaceFilesDep = Annotated[WorkspaceFileService, Depends(get_workspace_files)]


def get_agent_service(
    container: Annotated[AppContainer, Depends(get_container)],
    access: Annotated[AccessContext, Depends(get_access)],
) -> Iterator[AgentService]:
    """Open one catalog session for agent reads and writes."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    service: AgentService = AgentService(session, access)
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


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]


def get_agent_definition_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[AgentDefinitionService]:
    """Open one catalog session for definition file reads."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    service: AgentDefinitionService = AgentDefinitionService(session)
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


AgentDefinitionServiceDep = Annotated[AgentDefinitionService, Depends(get_agent_definition_service)]


def get_agent_definition_listing(
    container: Annotated[AppContainer, Depends(get_container)],
) -> AgentDefinitionListing:
    """List definition objects from the definition object store."""
    listing: AgentDefinitionListing = AgentDefinitionListing(
        container.agent_definition_store(),
        container.settings().agent_definitions,
    )
    return listing


AgentDefinitionListingDep = Annotated[AgentDefinitionListing, Depends(get_agent_definition_listing)]


def get_agent_pack(
    container: Annotated[AppContainer, Depends(get_container)],
    access: Annotated[AccessContext, Depends(get_access)],
) -> Iterator[AgentPackService]:
    """Open one catalog session for agent markdown beside an agent declaration."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    service: AgentPackService = AgentPackService(
        session,
        container.agent_definition_store(),
        container.settings().agent_definitions,
        access,
    )
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


AgentPackDep = Annotated[AgentPackService, Depends(get_agent_pack)]


def get_notebook_kernels(
    container: Annotated[AppContainer, Depends(get_container)],
) -> Iterator[NotebookKernelService]:
    """Open one catalog session for notebook kernel calls."""
    database: CatalogDatabase = container.database()
    session: Session = database.session()
    settings: AppSettings = container.settings()
    files: WorkspaceFileService = WorkspaceFileService(
        session,
        container.object_store(),
        settings.object_store.max_upload_bytes,
    )
    service: NotebookKernelService = NotebookKernelService(
        session=session,
        files=files,
        gateway=container.kernel_gateway(),
        directory=container.kernel_directory(),
        tickets=container.kernel_tickets(),
        settings=settings,
        logger=container.logger().getChild("notebook"),
    )
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


NotebookKernelDep = Annotated[NotebookKernelService, Depends(get_notebook_kernels)]


def require_auth(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
) -> None:
    """Require a bearer token when auth is enabled. No catalog session is opened."""
    if not container.settings().auth.enabled:
        return
    container.oidc_verifier().verify(_bearer(request))


def _bearer(request: Request) -> str:
    header: str | None = request.headers.get("authorization")
    if header is None:
        raise AuthenticationError("authentication required")
    prefix: str = "bearer "
    if not header.lower().startswith(prefix):
        raise AuthenticationError("authentication required")
    token: str = header[len(prefix) :].strip()
    if token == "":
        raise AuthenticationError("authentication required")
    return token
