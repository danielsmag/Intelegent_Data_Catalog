"""Definition objects in the store, scanned files, and a manual scan."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from intelegent_data_catalog.agents.definitions.models import (
    AgentDefinitionFileDetail,
    AgentDefinitionFileRead,
    AgentDefinitionObjectRead,
    AgentDefinitionScanQueued,
    AgentDefinitionTextRead,
    DefinitionFolder,
)
from intelegent_data_catalog.api.dependencies import (
    AgentDefinitionListingDep,
    AgentDefinitionServiceDep,
)

router: APIRouter = APIRouter()


@router.get("/agent-definitions/objects", response_model=list[AgentDefinitionObjectRead])
def list_agent_definition_objects(
    listing: AgentDefinitionListingDep,
    service: AgentDefinitionServiceDep,
    folder: Annotated[DefinitionFolder, Query()],
) -> list[AgentDefinitionObjectRead]:
    """List files under one definition folder, with the catalog agents each file includes."""
    objects: list[AgentDefinitionObjectRead] = listing.list_folder(folder)
    keys: list[str] = [item.key for item in objects]
    included: dict[str, list[UUID]] = service.agent_ids_by_key(keys)
    file_ids: dict[str, UUID] = service.file_ids_by_key(keys)
    linked: list[AgentDefinitionObjectRead] = [
        item.model_copy(
            update={
                "agent_ids": included.get(item.key, []),
                "file_id": file_ids.get(item.key),
            },
        )
        for item in objects
    ]
    return linked


@router.get("/agent-definitions/files", response_model=list[AgentDefinitionFileRead])
def list_agent_definition_files(
    service: AgentDefinitionServiceDep,
    folder: Annotated[DefinitionFolder | None, Query()] = None,
) -> list[AgentDefinitionFileRead]:
    """List scanned definition files. ``folder`` limits the kind."""
    return service.list_files(folder)


@router.get("/agent-definitions/text", response_model=AgentDefinitionTextRead)
def read_agent_definition_text(
    listing: AgentDefinitionListingDep,
    key: Annotated[str, Query(min_length=1)],
) -> AgentDefinitionTextRead:
    """Return the script stored at one definition object key."""
    source: str = listing.read_text(key)
    return AgentDefinitionTextRead(key=key, source=source)


@router.get(
    "/agent-definitions/files/{file_id}",
    response_model=AgentDefinitionFileDetail,
)
def read_agent_definition_file(
    file_id: UUID,
    service: AgentDefinitionServiceDep,
    listing: AgentDefinitionListingDep,
) -> AgentDefinitionFileDetail:
    """Return one definition file, its script, and the objects it declared."""
    detail: AgentDefinitionFileDetail = service.read(file_id)
    source: str = listing.read_text(detail.file.key)
    return detail.model_copy(update={"source": source})


@router.post(
    "/agent-definitions/scan",
    response_model=AgentDefinitionScanQueued,
    status_code=202,
)
def scan_agent_definitions_now(
    service: AgentDefinitionServiceDep,
) -> AgentDefinitionScanQueued:
    """Queue a scan of the definition prefix."""
    service.enqueue_scan()
    return AgentDefinitionScanQueued(queued=True)
