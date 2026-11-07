"""Share one catalog session across the independent registry services in tests."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.services.bindings import BindingService
from intelegent_data_catalog.services.data_sources import DataSourceService
from intelegent_data_catalog.services.engines import EngineService
from intelegent_data_catalog.services.relationships import RelationshipService
from intelegent_data_catalog.services.sync_runs import SyncRunService
from intelegent_data_catalog.services.tables import TableService


@dataclass
class OpenRegistry:
    """Services that share one repository and one session."""

    sources: DataSourceService
    tables: TableService
    relationships: RelationshipService
    engines: EngineService
    bindings: BindingService
    sync_runs: SyncRunService


def open_registry(session: Session) -> OpenRegistry:
    """Build every catalog service on the caller's session."""
    repo: RegistryRepository = RegistryRepository(session)
    return OpenRegistry(
        sources=DataSourceService(repo),
        tables=TableService(repo),
        relationships=RelationshipService(repo),
        engines=EngineService(repo),
        bindings=BindingService(repo),
        sync_runs=SyncRunService(repo),
    )
