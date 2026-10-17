"""Workspace file tree routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile

from intelegent_data_catalog.api.dependencies import AccessDep, WorkspaceFilesDep
from intelegent_data_catalog.persistence.models import WorkspaceNodeRow
from intelegent_data_catalog.registry.schemas import (
    FolderCreate,
    NodeUpdate,
    TextFileCreate,
    WorkspaceNodeRead,
)

router: APIRouter = APIRouter()


@router.get("/workspaces/{workspace_id}/nodes", response_model=list[WorkspaceNodeRead])
def list_nodes(
    workspace_id: UUID,
    service: WorkspaceFilesDep,
    access: AccessDep,
    parent_id: Annotated[UUID | None, Query()] = None,
) -> list[WorkspaceNodeRead]:
    """List folders and files under a parent. Omit the parent for the roots."""
    return service.list_nodes(workspace_id, parent_id, access)


@router.post(
    "/workspaces/{workspace_id}/nodes/folders",
    response_model=WorkspaceNodeRead,
    status_code=201,
)
def create_folder(
    workspace_id: UUID,
    body: FolderCreate,
    service: WorkspaceFilesDep,
    access: AccessDep,
) -> WorkspaceNodeRead:
    """Create a folder."""
    return service.create_folder(workspace_id, body, access)


@router.post(
    "/workspaces/{workspace_id}/nodes/files/text",
    response_model=WorkspaceNodeRead,
    status_code=201,
)
def create_text_file(
    workspace_id: UUID,
    body: TextFileCreate,
    service: WorkspaceFilesDep,
    access: AccessDep,
) -> WorkspaceNodeRead:
    """Create a text file."""
    return service.create_text_file(workspace_id, body, access)


@router.post(
    "/workspaces/{workspace_id}/nodes/files",
    response_model=WorkspaceNodeRead,
    status_code=201,
)
def upload_file(
    workspace_id: UUID,
    service: WorkspaceFilesDep,
    access: AccessDep,
    parent_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> WorkspaceNodeRead:
    """Upload a file into a folder."""
    payload: bytes = file.file.read()
    filename: str = file.filename or "upload"
    return service.upload_file(
        workspace_id,
        parent_id,
        filename,
        payload,
        file.content_type,
        access,
    )


@router.get("/workspaces/{workspace_id}/nodes/{node_id}", response_model=WorkspaceNodeRead)
def read_node(
    workspace_id: UUID,
    node_id: UUID,
    service: WorkspaceFilesDep,
    access: AccessDep,
) -> WorkspaceNodeRead:
    """Return one node."""
    return service.get_node(workspace_id, node_id, access)


@router.get("/workspaces/{workspace_id}/nodes/{node_id}/content")
def read_content(
    workspace_id: UUID,
    node_id: UUID,
    service: WorkspaceFilesDep,
    access: AccessDep,
) -> Response:
    """Stream file bytes."""
    node: WorkspaceNodeRow
    payload: bytes
    node, payload = service.read_content(workspace_id, node_id, access)
    media: str = node.content_type or "application/octet-stream"
    disposition: str = f'inline; filename="{_filename(node.name)}"'
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": disposition},
    )


@router.put("/workspaces/{workspace_id}/nodes/{node_id}/content", response_model=WorkspaceNodeRead)
async def replace_content(
    workspace_id: UUID,
    node_id: UUID,
    request: Request,
    service: WorkspaceFilesDep,
    access: AccessDep,
) -> WorkspaceNodeRead:
    """Replace file bytes."""
    payload: bytes = await request.body()
    return service.replace_content(workspace_id, node_id, payload, access)


@router.patch("/workspaces/{workspace_id}/nodes/{node_id}", response_model=WorkspaceNodeRead)
def patch_node(
    workspace_id: UUID,
    node_id: UUID,
    body: NodeUpdate,
    service: WorkspaceFilesDep,
    access: AccessDep,
) -> WorkspaceNodeRead:
    """Rename or move a node."""
    return service.update_node(workspace_id, node_id, body, access)


@router.delete("/workspaces/{workspace_id}/nodes/{node_id}", status_code=204)
def remove_node(
    workspace_id: UUID,
    node_id: UUID,
    service: WorkspaceFilesDep,
    access: AccessDep,
) -> Response:
    """Delete a node and its descendants."""
    service.delete_node(workspace_id, node_id, access)
    return Response(status_code=204)


def _filename(name: str) -> str:
    cleaned: str = name.replace('"', "").replace("\r", "").replace("\n", "")
    if cleaned == "":
        return "download"
    return cleaned
