"""CRUD routes for the PostgreSQL application catalog."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from intelegent_data_catalog.api.dependencies import RegistryServiceDep, ScopeAttachmentDep
from intelegent_data_catalog.registry.schemas import (
    AssetBindingCreate,
    AssetBindingRead,
    AssetBindingUpdate,
    CatalogTableCreate,
    CatalogTableRead,
    CatalogTableUpdate,
    ColumnCreate,
    ColumnRead,
    ColumnUpdate,
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    EngineConnectionCreate,
    EngineConnectionRead,
    EngineConnectionUpdate,
    RelationshipCreate,
    RelationshipRead,
    RelationshipUpdate,
    ScopeAttachmentCreate,
    ScopeAttachmentResult,
    ScopeCreate,
    ScopeRead,
    ScopeUpdate,
    SourceBindingCreate,
    SourceBindingRead,
    SourceBindingUpdate,
    SpaceCreate,
    SpaceRead,
    SpaceUpdate,
    SyncRunCreate,
    SyncRunRead,
    SyncRunUpdate,
    TableRelationships,
)

router: APIRouter = APIRouter()


@router.get("/spaces", response_model=list[SpaceRead])
def list_spaces(service: RegistryServiceDep) -> list[SpaceRead]:
    """List business spaces."""
    return service.list_spaces()


@router.post("/spaces", response_model=SpaceRead, status_code=201)
def create_space(body: SpaceCreate, service: RegistryServiceDep) -> SpaceRead:
    """Create a business space."""
    return service.create_space(body)


@router.get("/spaces/{space_id}", response_model=SpaceRead)
def read_space(space_id: UUID, service: RegistryServiceDep) -> SpaceRead:
    """Return one space."""
    return service.get_space(space_id)


@router.patch("/spaces/{space_id}", response_model=SpaceRead)
def patch_space(
    space_id: UUID,
    body: SpaceUpdate,
    service: RegistryServiceDep,
) -> SpaceRead:
    """Patch a space."""
    return service.update_space(space_id, body)


@router.delete("/spaces/{space_id}", status_code=204)
def remove_space(space_id: UUID, service: RegistryServiceDep) -> Response:
    """Delete a space that has no scopes."""
    service.delete_space(space_id)
    return Response(status_code=204)


@router.get("/spaces/{space_id}/scopes", response_model=list[ScopeRead])
def list_scopes(space_id: UUID, service: RegistryServiceDep) -> list[ScopeRead]:
    """List scopes in a space."""
    return service.list_scopes(space_id)


@router.post("/spaces/{space_id}/scopes", response_model=ScopeRead, status_code=201)
def create_scope(
    space_id: UUID,
    body: ScopeCreate,
    service: RegistryServiceDep,
) -> ScopeRead:
    """Create a scope in a space."""
    return service.create_scope(space_id, body)


@router.get("/scopes/{scope_id}", response_model=ScopeRead)
def read_scope(scope_id: UUID, service: RegistryServiceDep) -> ScopeRead:
    """Return one scope."""
    return service.get_scope(scope_id)


@router.patch("/scopes/{scope_id}", response_model=ScopeRead)
def patch_scope(
    scope_id: UUID,
    body: ScopeUpdate,
    service: RegistryServiceDep,
) -> ScopeRead:
    """Patch a scope."""
    return service.update_scope(scope_id, body)


@router.delete("/scopes/{scope_id}", status_code=204)
def remove_scope(scope_id: UUID, service: RegistryServiceDep) -> Response:
    """Delete a scope that has no tables."""
    service.delete_scope(scope_id)
    return Response(status_code=204)


@router.get("/data-sources", response_model=list[DataSourceRead])
def list_data_sources(service: RegistryServiceDep) -> list[DataSourceRead]:
    """List registered data sources."""
    return service.list_data_sources()


@router.post("/data-sources", response_model=DataSourceRead, status_code=201)
def create_data_source(body: DataSourceCreate, service: RegistryServiceDep) -> DataSourceRead:
    """Register a data source. This does not provision a connector."""
    return service.create_data_source(body)


@router.get("/data-sources/{data_source_id}", response_model=DataSourceRead)
def read_data_source(data_source_id: UUID, service: RegistryServiceDep) -> DataSourceRead:
    """Return one data source."""
    return service.get_data_source(data_source_id)


@router.patch("/data-sources/{data_source_id}", response_model=DataSourceRead)
def patch_data_source(
    data_source_id: UUID,
    body: DataSourceUpdate,
    service: RegistryServiceDep,
) -> DataSourceRead:
    """Patch a data source. Secrets stay in credential_ref."""
    return service.update_data_source(data_source_id, body)


@router.delete("/data-sources/{data_source_id}", status_code=204)
def remove_data_source(data_source_id: UUID, service: RegistryServiceDep) -> Response:
    """Delete a data source that has no tables or bindings."""
    service.delete_data_source(data_source_id)
    return Response(status_code=204)


@router.get("/scopes/{scope_id}/tables", response_model=list[CatalogTableRead])
def list_scope_tables(scope_id: UUID, service: RegistryServiceDep) -> list[CatalogTableRead]:
    """List catalog tables in a scope."""
    return service.list_tables(scope_id)


@router.post("/scopes/{scope_id}/attachments", response_model=ScopeAttachmentResult)
def attach_scope_assets(
    scope_id: UUID,
    body: ScopeAttachmentCreate,
    service: ScopeAttachmentDep,
) -> ScopeAttachmentResult:
    """Copy tables, or a whole schema, from Polaris or Trino into this scope."""
    return service.attach(scope_id, body)


@router.post("/scopes/{scope_id}/tables", response_model=CatalogTableRead, status_code=201)
def create_scope_table(
    scope_id: UUID,
    body: CatalogTableCreate,
    service: RegistryServiceDep,
) -> CatalogTableRead:
    """Create a catalog table in a scope."""
    return service.create_table(scope_id, body)


@router.get("/tables/{table_id}", response_model=CatalogTableRead)
def read_catalog_table(table_id: UUID, service: RegistryServiceDep) -> CatalogTableRead:
    """Return one catalog table."""
    return service.get_table(table_id)


@router.patch("/tables/{table_id}", response_model=CatalogTableRead)
def patch_catalog_table(
    table_id: UUID,
    body: CatalogTableUpdate,
    service: RegistryServiceDep,
) -> CatalogTableRead:
    """Patch user-facing table fields and the manual scope assignment."""
    return service.update_table(table_id, body)


@router.delete("/tables/{table_id}", status_code=204)
def remove_catalog_table(table_id: UUID, service: RegistryServiceDep) -> Response:
    """Delete a catalog table that is not used by a relationship."""
    service.delete_table(table_id)
    return Response(status_code=204)


@router.get("/tables/{table_id}/columns", response_model=list[ColumnRead])
def list_table_columns(table_id: UUID, service: RegistryServiceDep) -> list[ColumnRead]:
    """List columns on a catalog table."""
    return service.list_columns(table_id)


@router.post("/tables/{table_id}/columns", response_model=ColumnRead, status_code=201)
def create_table_column(
    table_id: UUID,
    body: ColumnCreate,
    service: RegistryServiceDep,
) -> ColumnRead:
    """Add a column to a catalog table."""
    return service.create_column(table_id, body)


@router.patch("/columns/{column_id}", response_model=ColumnRead)
def patch_column(
    column_id: UUID,
    body: ColumnUpdate,
    service: RegistryServiceDep,
) -> ColumnRead:
    """Patch user column fields."""
    return service.update_column(column_id, body)


@router.delete("/columns/{column_id}", status_code=204)
def remove_column(column_id: UUID, service: RegistryServiceDep) -> Response:
    """Delete a column that is not used by a relationship."""
    service.delete_column(column_id)
    return Response(status_code=204)


@router.post("/relationships", response_model=RelationshipRead, status_code=201)
def create_relationship(
    body: RelationshipCreate,
    service: RegistryServiceDep,
) -> RelationshipRead:
    """Create a relationship and its column mappings."""
    return service.create_relationship(body)


@router.get("/relationships/{relationship_id}", response_model=RelationshipRead)
def read_relationship(relationship_id: UUID, service: RegistryServiceDep) -> RelationshipRead:
    """Return one relationship."""
    return service.get_relationship(relationship_id)


@router.patch("/relationships/{relationship_id}", response_model=RelationshipRead)
def patch_relationship(
    relationship_id: UUID,
    body: RelationshipUpdate,
    service: RegistryServiceDep,
) -> RelationshipRead:
    """Patch a relationship."""
    return service.update_relationship(relationship_id, body)


@router.delete("/relationships/{relationship_id}", status_code=204)
def remove_relationship(relationship_id: UUID, service: RegistryServiceDep) -> Response:
    """Delete a relationship."""
    service.delete_relationship(relationship_id)
    return Response(status_code=204)


@router.get("/tables/{table_id}/relationships", response_model=TableRelationships)
def read_table_relationships(
    table_id: UUID,
    service: RegistryServiceDep,
) -> TableRelationships:
    """Return incoming and outgoing relationships for a table."""
    return service.list_table_relationships(table_id)


@router.get("/engine-connections", response_model=list[EngineConnectionRead])
def list_engine_connections(service: RegistryServiceDep) -> list[EngineConnectionRead]:
    """List Trino and Spark SQL connections."""
    return service.list_engine_connections()


@router.post("/engine-connections", response_model=EngineConnectionRead, status_code=201)
def create_engine_connection(
    body: EngineConnectionCreate,
    service: RegistryServiceDep,
) -> EngineConnectionRead:
    """Register an engine connection."""
    return service.create_engine_connection(body)


@router.get("/engine-connections/{engine_connection_id}", response_model=EngineConnectionRead)
def read_engine_connection(
    engine_connection_id: UUID,
    service: RegistryServiceDep,
) -> EngineConnectionRead:
    """Return one engine connection."""
    return service.get_engine_connection(engine_connection_id)


@router.patch("/engine-connections/{engine_connection_id}", response_model=EngineConnectionRead)
def patch_engine_connection(
    engine_connection_id: UUID,
    body: EngineConnectionUpdate,
    service: RegistryServiceDep,
) -> EngineConnectionRead:
    """Patch an engine connection."""
    return service.update_engine_connection(engine_connection_id, body)


@router.delete("/engine-connections/{engine_connection_id}", status_code=204)
def remove_engine_connection(
    engine_connection_id: UUID,
    service: RegistryServiceDep,
) -> Response:
    """Delete an engine connection that has no source bindings."""
    service.delete_engine_connection(engine_connection_id)
    return Response(status_code=204)


@router.get("/source-bindings", response_model=list[SourceBindingRead])
def list_source_bindings(
    service: RegistryServiceDep,
    data_source_id: Annotated[UUID | None, Query()] = None,
    engine_connection_id: Annotated[UUID | None, Query()] = None,
) -> list[SourceBindingRead]:
    """List source engine bindings."""
    return service.list_source_bindings(data_source_id, engine_connection_id)


@router.post("/source-bindings", response_model=SourceBindingRead, status_code=201)
def create_source_binding(
    body: SourceBindingCreate,
    service: RegistryServiceDep,
) -> SourceBindingRead:
    """Bind a data source to an engine connection."""
    return service.create_source_binding(body)


@router.get("/source-bindings/{source_binding_id}", response_model=SourceBindingRead)
def read_source_binding(
    source_binding_id: UUID,
    service: RegistryServiceDep,
) -> SourceBindingRead:
    """Return one source binding."""
    return service.get_source_binding(source_binding_id)


@router.patch("/source-bindings/{source_binding_id}", response_model=SourceBindingRead)
def patch_source_binding(
    source_binding_id: UUID,
    body: SourceBindingUpdate,
    service: RegistryServiceDep,
) -> SourceBindingRead:
    """Patch a source binding."""
    return service.update_source_binding(source_binding_id, body)


@router.delete("/source-bindings/{source_binding_id}", status_code=204)
def remove_source_binding(source_binding_id: UUID, service: RegistryServiceDep) -> Response:
    """Delete a source binding that has no asset bindings or sync runs."""
    service.delete_source_binding(source_binding_id)
    return Response(status_code=204)


@router.get("/tables/{table_id}/bindings", response_model=list[AssetBindingRead])
def list_asset_bindings(table_id: UUID, service: RegistryServiceDep) -> list[AssetBindingRead]:
    """List discovery and query bindings for a table."""
    return service.list_asset_bindings(table_id)


@router.post("/tables/{table_id}/bindings", response_model=AssetBindingRead, status_code=201)
def create_asset_binding(
    table_id: UUID,
    body: AssetBindingCreate,
    service: RegistryServiceDep,
) -> AssetBindingRead:
    """Store an explicit engine address for a table."""
    return service.create_asset_binding(table_id, body)


@router.get("/bindings/{binding_id}", response_model=AssetBindingRead)
def read_asset_binding(binding_id: UUID, service: RegistryServiceDep) -> AssetBindingRead:
    """Return one asset binding."""
    return service.get_asset_binding(binding_id)


@router.patch("/bindings/{binding_id}", response_model=AssetBindingRead)
def patch_asset_binding(
    binding_id: UUID,
    body: AssetBindingUpdate,
    service: RegistryServiceDep,
) -> AssetBindingRead:
    """Patch an asset binding address or selection."""
    return service.update_asset_binding(binding_id, body)


@router.delete("/bindings/{binding_id}", status_code=204)
def remove_asset_binding(binding_id: UUID, service: RegistryServiceDep) -> Response:
    """Delete one asset binding."""
    service.delete_asset_binding(binding_id)
    return Response(status_code=204)


@router.get("/sync-runs", response_model=list[SyncRunRead])
def list_sync_runs(
    service: RegistryServiceDep,
    source_engine_binding_id: Annotated[UUID | None, Query()] = None,
) -> list[SyncRunRead]:
    """List recorded metadata sync runs."""
    return service.list_sync_runs(source_engine_binding_id)


@router.post("/sync-runs", response_model=SyncRunRead, status_code=201)
def create_sync_run(body: SyncRunCreate, service: RegistryServiceDep) -> SyncRunRead:
    """Record a metadata sync run. This does not contact Trino."""
    return service.create_sync_run(body)


@router.get("/sync-runs/{sync_run_id}", response_model=SyncRunRead)
def read_sync_run(sync_run_id: UUID, service: RegistryServiceDep) -> SyncRunRead:
    """Return one sync run."""
    return service.get_sync_run(sync_run_id)


@router.patch("/sync-runs/{sync_run_id}", response_model=SyncRunRead)
def patch_sync_run(
    sync_run_id: UUID,
    body: SyncRunUpdate,
    service: RegistryServiceDep,
) -> SyncRunRead:
    """Update a recorded sync run."""
    return service.update_sync_run(sync_run_id, body)
