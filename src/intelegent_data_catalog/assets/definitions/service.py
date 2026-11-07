"""Read definition scan rows and enqueue a scan."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.models import (
    AssetDefinitionFileDetail,
    AssetDefinitionFileRead,
    AssetDefinitionItemRead,
    DefinitionFolder,
)
from intelegent_data_catalog.assets.definitions.repository import AssetDefinitionRepository
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.enums import (
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
)
from intelegent_data_catalog.persistence.models import (
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
)

_FOLDERS: dict[DefinitionFolder, DefinitionKind] = {
    "assets": DefinitionKind.ASSET,
}


class AssetDefinitionService:
    """Lists scanned files and asks a worker to scan now."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session
        self._repository: AssetDefinitionRepository = AssetDefinitionRepository(session)

    def list_files(self, folder: DefinitionFolder | None) -> list[AssetDefinitionFileRead]:
        """Return scanned files. ``folder`` limits the kind."""
        kind: DefinitionKind | None = None if folder is None else _FOLDERS[folder]
        return [_file_read(row) for row in self._repository.list_files(kind)]

    def file_ids_by_key(self, keys: list[str]) -> dict[str, UUID]:
        """Return the scan row id for each definition file key."""
        return self._repository.file_ids_by_key(keys)

    def asset_ids_by_key(self, keys: list[str]) -> dict[str, list[UUID]]:
        """Return catalog assets each definition file key currently includes."""
        return self._repository.asset_ids_by_key(keys)

    def read(self, file_id: UUID) -> AssetDefinitionFileDetail:
        """Return one file and its declarations."""
        row: AssetDefinitionFileRow | None = self._repository.get(file_id)
        if row is None:
            raise EntityNotFoundError("asset definition file", str(file_id))
        items: list[AssetDefinitionItemRead] = [
            _item_read(item) for item in self._repository.items_for(file_id)
        ]
        return AssetDefinitionFileDetail(file=_file_read(row), items=items)

    def enqueue_scan(self) -> None:
        """Queue one scan. Beat is not required."""
        from intelegent_data_catalog.jobs.celery_app import celery_app
        from intelegent_data_catalog.jobs.names import SCAN_ASSET_DEFINITIONS

        celery_app.tasks[SCAN_ASSET_DEFINITIONS].delay()


def _file_read(row: AssetDefinitionFileRow) -> AssetDefinitionFileRead:
    return AssetDefinitionFileRead(
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


def _item_read(row: AssetDefinitionItemRow) -> AssetDefinitionItemRead:
    return AssetDefinitionItemRead(
        id=row.id,
        kind=DefinitionKind(row.kind),
        item_key=row.item_key,
        status=DefinitionItemStatus(row.status),
        error=row.error,
        asset_id=row.asset_id,
        run_status=None if row.run_status is None else DefinitionRunStatus(row.run_status),
        run_error=row.run_error,
        ran_at=row.ran_at,
        schedule=row.schedule,
        next_run_at=row.next_run_at,
    )
