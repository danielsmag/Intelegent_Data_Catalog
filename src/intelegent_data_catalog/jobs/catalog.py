"""Run one knowledge-context build on its own database session."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.knowledge.models import ScopeContextOptions, TableContextOptions
from intelegent_data_catalog.persistence.database import CatalogDatabase


class CatalogKnowledgeBuild:
    """Adapter from the job queue to ``RequestPlatform``."""

    def __init__(self, container: AppContainer) -> None:
        self._container: AppContainer = container

    def run(self, subject_id: UUID, options: TableContextOptions) -> None:
        """Build and save context. The request session is already closed."""
        table_id: UUID = subject_id
        database: CatalogDatabase = self._container.database()
        session: Session = database.session()
        try:
            platform: RequestPlatform = RequestPlatform(
                session,
                self._container.settings(),
                self._container.query_service(),
            )
            platform.build_knowledge_context(table_id, options)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class CatalogScopeBuild:
    """Adapter from the scope job queue to ``RequestPlatform``."""

    def __init__(self, container: AppContainer) -> None:
        self._container: AppContainer = container

    def run(self, subject_id: UUID, options: ScopeContextOptions) -> None:
        """Build and save a scope context. The request session is already closed."""
        database: CatalogDatabase = self._container.database()
        session: Session = database.session()
        try:
            platform: RequestPlatform = RequestPlatform(
                session,
                self._container.settings(),
                self._container.query_service(),
            )
            platform.build_scope_context(subject_id, options)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
