"""Run one knowledge-context build on its own database session."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.knowledge.models import TableContextOptions
from intelegent_data_catalog.persistence.database import CatalogDatabase


class CatalogKnowledgeBuild:
    """Adapter from the job queue to ``RequestPlatform``."""

    def __init__(self, container: AppContainer) -> None:
        self._container: AppContainer = container

    def run(self, table_id: UUID, options: TableContextOptions) -> None:
        """Build and save context. The request session is already closed."""
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
