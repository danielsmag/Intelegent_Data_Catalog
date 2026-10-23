"""Definition objects in the store, scanned files, and a manual scan."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from intelegent_data_catalog.api.dependencies import (
    AssetDefinitionListingDep,
    AssetDefinitionServiceDep,
)
from intelegent_data_catalog.assets.definitions.models import (
    AssetDefinitionFileDetail,
    AssetDefinitionFileRead,
    AssetDefinitionObjectRead,
    AssetDefinitionScanQueued,
    AssetDefinitionTextRead,
    DefinitionFolder,
)

router: APIRouter = APIRouter()


@router.get("/asset-definitions/objects", response_model=list[AssetDefinitionObjectRead])
def list_asset_definition_objects(
    listing: AssetDefinitionListingDep,
    service: AssetDefinitionServiceDep,
    folder: Annotated[DefinitionFolder, Query()],
) -> list[AssetDefinitionObjectRead]:
    """List files under one definition folder, with the catalog assets each file includes."""
    objects: list[AssetDefinitionObjectRead] = listing.list_folder(folder)
    keys: list[str] = [item.key for item in objects]
    included: dict[str, list[UUID]] = service.asset_ids_by_key(keys)
    file_ids: dict[str, UUID] = service.file_ids_by_key(keys)
    linked: list[AssetDefinitionObjectRead] = [
        item.model_copy(
            update={
                "asset_ids": included.get(item.key, []),
                "file_id": file_ids.get(item.key),
            },
        )
        for item in objects
    ]
    return linked


@router.get("/asset-definitions/files", response_model=list[AssetDefinitionFileRead])
def list_asset_definition_files(
    service: AssetDefinitionServiceDep,
    folder: Annotated[DefinitionFolder | None, Query()] = None,
) -> list[AssetDefinitionFileRead]:
    """List scanned definition files. ``folder`` limits the kind."""
    return service.list_files(folder)


@router.get("/asset-definitions/text", response_model=AssetDefinitionTextRead)
def read_asset_definition_text(
    listing: AssetDefinitionListingDep,
    key: Annotated[str, Query(min_length=1)],
) -> AssetDefinitionTextRead:
    """Return the script stored at one definition object key."""
    source: str = listing.read_text(key)
    return AssetDefinitionTextRead(key=key, source=source)


@router.get(
    "/asset-definitions/files/{file_id}",
    response_model=AssetDefinitionFileDetail,
)
def read_asset_definition_file(
    file_id: UUID,
    service: AssetDefinitionServiceDep,
    listing: AssetDefinitionListingDep,
) -> AssetDefinitionFileDetail:
    """Return one definition file, its script, and the objects it declared."""
    detail: AssetDefinitionFileDetail = service.read(file_id)
    source: str = listing.read_text(detail.file.key)
    return detail.model_copy(update={"source": source})


@router.post(
    "/asset-definitions/scan",
    response_model=AssetDefinitionScanQueued,
    status_code=202,
)
def scan_asset_definitions_now(
    service: AssetDefinitionServiceDep,
) -> AssetDefinitionScanQueued:
    """Queue a scan of the definition prefix."""
    service.enqueue_scan()
    return AssetDefinitionScanQueued(queued=True)
