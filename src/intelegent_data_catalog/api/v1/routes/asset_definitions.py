"""Scanned asset definition files and a manual scan."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from intelegent_data_catalog.api.dependencies import AssetDefinitionServiceDep
from intelegent_data_catalog.assets.definitions.models import (
    AssetDefinitionFileDetail,
    AssetDefinitionFileRead,
    AssetDefinitionScanQueued,
    DefinitionFolder,
)

router: APIRouter = APIRouter()


@router.get("/asset-definitions/files", response_model=list[AssetDefinitionFileRead])
def list_asset_definition_files(
    service: AssetDefinitionServiceDep,
    folder: Annotated[DefinitionFolder | None, Query()] = None,
) -> list[AssetDefinitionFileRead]:
    """List scanned definition files. ``folder`` limits the kind."""
    return service.list_files(folder)


@router.get(
    "/asset-definitions/files/{file_id}",
    response_model=AssetDefinitionFileDetail,
)
def read_asset_definition_file(
    file_id: UUID,
    service: AssetDefinitionServiceDep,
) -> AssetDefinitionFileDetail:
    """Return one definition file and the objects it declared."""
    return service.read(file_id)


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
