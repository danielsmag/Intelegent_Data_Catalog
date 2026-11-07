"""Saved refresh settings for tables and assets."""

from uuid import UUID

from fastapi import APIRouter

from intelegent_data_catalog.api.dependencies import EntityConfigurationDep
from intelegent_data_catalog.entity_configuration import (
    EntityConfigurationRead,
    EntityConfigurationWrite,
)
from intelegent_data_catalog.persistence.enums import RefreshSubjectKind

router: APIRouter = APIRouter()










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
