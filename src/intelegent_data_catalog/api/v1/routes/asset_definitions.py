"""Asset definition objects in the store, scanned files, and a manual scan."""

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
    """List files under the assets folder, with the assets each file includes."""
    objects: list[AssetDefinitionObjectRead] = listing.list_folder(folder)
    keys: list[str] = [item.key for item in objects]
    included: dict[str, list[UUID]] = service.asset_ids_by_key(keys)
    file_ids: dict[str, UUID] = service.file_ids_by_key(keys)
    return [
        item.model_copy(
            update={
                "asset_ids": included.get(item.key, []),
                "file_id": file_ids.get(item.key),
            },
        )
        for item in objects
    ]


@router.get("/asset-definitions/files", response_model=list[AssetDefinitionFileRead])
def list_asset_definition_files(
    service: AssetDefinitionServiceDep,
) -> list[AssetDefinitionFileRead]:
    """List scanned asset definition files."""
    return service.list_files()


@router.get("/asset-definitions/text", response_model=AssetDefinitionTextRead)
def read_asset_definition_text(
    listing: AssetDefinitionListingDep,
    key: Annotated[str, Query(min_length=1)],
) -> AssetDefinitionTextRead:
    """Return the script stored at one definition object key."""
    return AssetDefinitionTextRead(key=key, source=listing.read_text(key))


@router.get("/asset-definitions/files/{file_id}", response_model=AssetDefinitionFileDetail)
def read_asset_definition_file(
    file_id: UUID,
    service: AssetDefinitionServiceDep,
    listing: AssetDefinitionListingDep,
) -> AssetDefinitionFileDetail:
    """Return one definition file, its script, and the assets it declared."""
    detail: AssetDefinitionFileDetail = service.read(file_id)
    return detail.model_copy(update={"source": listing.read_text(detail.file.key)})


@router.post(
    "/asset-definitions/scan",
    response_model=AssetDefinitionScanQueued,
    status_code=202,
)
def scan_asset_definitions_now(
    service: AssetDefinitionServiceDep,
) -> AssetDefinitionScanQueued:
    """Queue a scan of the asset definition prefix."""
    service.enqueue_scan()
    return AssetDefinitionScanQueued(queued=True)
