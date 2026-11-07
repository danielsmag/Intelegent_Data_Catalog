"""Read definition scan rows and enqueue a scan."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.definitions.models import (
    AgentDefinitionFileDetail,
    AgentDefinitionFileRead,
    AgentDefinitionItemRead,
    DefinitionFolder,
)
from intelegent_data_catalog.agents.definitions.repository import AgentDefinitionRepository
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.enums import (
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
)
from intelegent_data_catalog.persistence.models import (
    AgentDefinitionFileRow,
    AgentDefinitionItemRow,
)

_FOLDERS: dict[DefinitionFolder, DefinitionKind] = {
    "agents": DefinitionKind.AGENT,
}


class AgentDefinitionService:
    """Lists scanned files and asks a worker to scan now."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session
        self._repository: AgentDefinitionRepository = AgentDefinitionRepository(session)

    def list_files(self, folder: DefinitionFolder | None) -> list[AgentDefinitionFileRead]:
        """Return scanned files. ``folder`` limits the kind."""
        kind: DefinitionKind | None = None if folder is None else _FOLDERS[folder]
        return [_file_read(row) for row in self._repository.list_files(kind)]

    def file_ids_by_key(self, keys: list[str]) -> dict[str, UUID]:
        """Return the scan row id for each definition file key."""
        return self._repository.file_ids_by_key(keys)

    def agent_ids_by_key(self, keys: list[str]) -> dict[str, list[UUID]]:
        """Return catalog agents each definition file key currently includes."""
        return self._repository.agent_ids_by_key(keys)

    def read(self, file_id: UUID) -> AgentDefinitionFileDetail:
        """Return one file and its declarations."""
        row: AgentDefinitionFileRow | None = self._repository.get(file_id)
        if row is None:
            raise EntityNotFoundError("agent definition file", str(file_id))
        items: list[AgentDefinitionItemRead] = [
            _item_read(item) for item in self._repository.items_for(file_id)
        ]
        return AgentDefinitionFileDetail(file=_file_read(row), items=items)

    def enqueue_scan(self) -> None:
        """Queue one scan. Beat is not required."""
        from intelegent_data_catalog.jobs.celery_app import celery_app
        from intelegent_data_catalog.jobs.names import SCAN_AGENT_DEFINITIONS

        celery_app.tasks[SCAN_AGENT_DEFINITIONS].delay()


def _file_read(row: AgentDefinitionFileRow) -> AgentDefinitionFileRead:
    return AgentDefinitionFileRead(
        id=row.id,
        key=row.key,
        folder_kind=DefinitionKind(row.folder_kind),
        status=DefinitionFileStatus(row.status),
        error=row.error,
        item_count=row.item_count,
        etag=row.etag,
        size_bytes=row.size_bytes,
        scanned_at=row.scanned_at,
        parsed_at=row.parsed_at,
    )


def _item_read(row: AgentDefinitionItemRow) -> AgentDefinitionItemRead:
    return AgentDefinitionItemRead(
        id=row.id,
        kind=DefinitionKind(row.kind),
        item_key=row.item_key,
        status=DefinitionItemStatus(row.status),
        error=row.error,
        agent_id=row.agent_id,
    )
