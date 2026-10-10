"""Saved refresh settings for spaces, scopes, and tables."""

from uuid import UUID

from fastapi import APIRouter

from intelegent_data_catalog.api.dependencies import EntityConfigurationDep
from intelegent_data_catalog.entity_configuration import (
    EntityConfigurationRead,
    EntityConfigurationWrite,
)
from intelegent_data_catalog.persistence.enums import RefreshSubjectKind

router: APIRouter = APIRouter()


@router.get("/spaces/{space_id}/configuration", response_model=EntityConfigurationRead)
def read_space_configuration(
    space_id: UUID,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Return the refresh settings for one space."""
    return service.get(RefreshSubjectKind.SPACE, space_id)


@router.put("/spaces/{space_id}/configuration", response_model=EntityConfigurationRead)
def put_space_configuration(
    space_id: UUID,
    body: EntityConfigurationWrite,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Save the refresh settings for one space."""
    return service.put(RefreshSubjectKind.SPACE, space_id, body)


@router.get("/scopes/{scope_id}/configuration", response_model=EntityConfigurationRead)
def read_scope_configuration(
    scope_id: UUID,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Return the refresh settings for one scope."""
    return service.get(RefreshSubjectKind.SCOPE, scope_id)


@router.put("/scopes/{scope_id}/configuration", response_model=EntityConfigurationRead)
def put_scope_configuration(
    scope_id: UUID,
    body: EntityConfigurationWrite,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Save the refresh settings for one scope."""
    return service.put(RefreshSubjectKind.SCOPE, scope_id, body)


@router.get("/tables/{table_id}/configuration", response_model=EntityConfigurationRead)
def read_table_configuration(
    table_id: UUID,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Return the refresh settings for one table."""
    return service.get(RefreshSubjectKind.TABLE, table_id)


@router.put("/tables/{table_id}/configuration", response_model=EntityConfigurationRead)
def put_table_configuration(
    table_id: UUID,
    body: EntityConfigurationWrite,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Save the refresh settings for one table."""
    return service.put(RefreshSubjectKind.TABLE, table_id, body)
