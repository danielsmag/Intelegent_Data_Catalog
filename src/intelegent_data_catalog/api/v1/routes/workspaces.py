"""Workspace administration routes."""

from uuid import UUID

from fastapi import APIRouter, Response

from intelegent_data_catalog.api.dependencies import AccessDep, WorkspaceServiceDep
from intelegent_data_catalog.registry.schemas import (
    MeRead,
    WorkspaceCreate,
    WorkspaceMemberCreate,
    WorkspaceMemberRead,
    WorkspaceRead,
    WorkspaceUpdate,
)

router: APIRouter = APIRouter()


@router.get("/me", response_model=MeRead)
def read_me(service: WorkspaceServiceDep, access: AccessDep) -> MeRead:
    """Return the caller and the workspaces they can open."""
    return service.me(access)


@router.get("/workspaces", response_model=list[WorkspaceRead])
def list_workspaces(service: WorkspaceServiceDep, access: AccessDep) -> list[WorkspaceRead]:
    """List workspaces the caller can open."""
    return service.list_workspaces(access)


@router.post("/workspaces", response_model=WorkspaceRead, status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    service: WorkspaceServiceDep,
    access: AccessDep,
) -> WorkspaceRead:
    """Create a workspace. Platform admins only."""
    return service.create_workspace(body, access)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRead)
def read_workspace(
    workspace_id: UUID,
    service: WorkspaceServiceDep,
    access: AccessDep,
) -> WorkspaceRead:
    """Return one workspace."""
    return service.get_workspace(workspace_id, access)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceRead)
def patch_workspace(
    workspace_id: UUID,
    body: WorkspaceUpdate,
    service: WorkspaceServiceDep,
    access: AccessDep,
) -> WorkspaceRead:
    """Patch a workspace."""
    return service.update_workspace(workspace_id, body, access)


@router.delete("/workspaces/{workspace_id}", status_code=204)
def remove_workspace(
    workspace_id: UUID,
    service: WorkspaceServiceDep,
    access: AccessDep,
) -> Response:
    """Delete a workspace."""
    service.delete_workspace(workspace_id, access)
    return Response(status_code=204)


@router.get("/workspaces/{workspace_id}/members", response_model=list[WorkspaceMemberRead])
def list_members(
    workspace_id: UUID,
    service: WorkspaceServiceDep,
    access: AccessDep,
) -> list[WorkspaceMemberRead]:
    """List workspace members."""
    return service.list_members(workspace_id, access)


@router.post(
    "/workspaces/{workspace_id}/members",
    response_model=WorkspaceMemberRead,
    status_code=201,
)
def add_member(
    workspace_id: UUID,
    body: WorkspaceMemberCreate,
    service: WorkspaceServiceDep,
    access: AccessDep,
) -> WorkspaceMemberRead:
    """Add a member by email."""
    return service.add_member(workspace_id, body, access)


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
def remove_member(
    workspace_id: UUID,
    user_id: UUID,
    service: WorkspaceServiceDep,
    access: AccessDep,
) -> Response:
    """Remove a member."""
    service.remove_member(workspace_id, user_id, access)
    return Response(status_code=204)






