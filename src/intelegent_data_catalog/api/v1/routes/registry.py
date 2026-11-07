"""CRUD routes for the PostgreSQL application catalog."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from intelegent_data_catalog.api.dependencies import (
    BindingServiceDep,
    DataSourceServiceDep,
    EngineServiceDep,
    RelationshipServiceDep,
    SyncRunServiceDep,
    TableServiceDep,
)
from intelegent_data_catalog.registry.schemas import (
    AgentBindingCreate,
    AgentBindingRead,
    AgentBindingUpdate,
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
    SourceBindingCreate,
    SourceBindingRead,
    SourceBindingUpdate,
    SyncRunCreate,
    SyncRunRead,
    SyncRunUpdate,
    TableRelationships,
)

router: APIRouter = APIRouter()












@router.get("/data-sources", response_model=list[DataSourceRead])
def list_data_sources(service: DataSourceServiceDep) -> list[DataSourceRead]:
    """List registered data sources."""
    return service.list_data_sources()


@router.post("/data-sources", response_model=DataSourceRead, status_code=201)
def create_data_source(body: DataSourceCreate, service: DataSourceServiceDep) -> DataSourceRead:
    """Register a data source. This does not provision a connector."""
    return service.create_data_source(body)


@router.get("/data-sources/{data_source_id}", response_model=DataSourceRead)
def read_data_source(data_source_id: UUID, service: DataSourceServiceDep) -> DataSourceRead:
    """Return one data source."""
    return service.get_data_source(data_source_id)


@router.patch("/data-sources/{data_source_id}", response_model=DataSourceRead)
def patch_data_source(
    data_source_id: UUID,
    body: DataSourceUpdate,
    service: DataSourceServiceDep,
) -> DataSourceRead:
    """Patch a data source. Secrets stay in credential_ref."""
    return service.update_data_source(data_source_id, body)


@router.delete("/data-sources/{data_source_id}", status_code=204)
def remove_data_source(data_source_id: UUID, service: DataSourceServiceDep) -> Response:
    """Delete a data source that has no tables or bindings."""
    service.delete_data_source(data_source_id)
    return Response(status_code=204)





@router.get("/tables", response_model=list[CatalogTableRead])
def list_catalog_tables(service: TableServiceDep) -> list[CatalogTableRead]:
    """List catalog tables."""
    return service.list_tables()


@router.post("/tables", response_model=CatalogTableRead, status_code=201)
def create_catalog_table(
    body: CatalogTableCreate,
    service: TableServiceDep,
) -> CatalogTableRead:
    """Create a catalog table."""
    return service.create_table(body)


@router.get("/tables/{table_id}", response_model=CatalogTableRead)
def read_catalog_table(table_id: UUID, service: TableServiceDep) -> CatalogTableRead:
    """Return one catalog table."""
    return service.get_table(table_id)


@router.patch("/tables/{table_id}", response_model=CatalogTableRead)
def patch_catalog_table(
    table_id: UUID,
    body: CatalogTableUpdate,
    service: TableServiceDep,
) -> CatalogTableRead:
    """Patch user-facing table fields."""
    return service.update_table(table_id, body)


@router.delete("/tables/{table_id}", status_code=204)
def remove_catalog_table(table_id: UUID, service: TableServiceDep) -> Response:
    """Delete a catalog table that is not used by a relationship."""
    service.delete_table(table_id)
    return Response(status_code=204)


@router.get("/tables/{table_id}/columns", response_model=list[ColumnRead])
def list_table_columns(table_id: UUID, service: TableServiceDep) -> list[ColumnRead]:
    """List columns on a catalog table."""
    return service.list_columns(table_id)


@router.post("/tables/{table_id}/columns", response_model=ColumnRead, status_code=201)
def create_table_column(
    table_id: UUID,
    body: ColumnCreate,
    service: TableServiceDep,
) -> ColumnRead:
    """Add a column to a catalog table."""
    return service.create_column(table_id, body)


@router.patch("/columns/{column_id}", response_model=ColumnRead)
def patch_column(
    column_id: UUID,
    body: ColumnUpdate,
    service: TableServiceDep,
) -> ColumnRead:
    """Patch user column fields."""
    return service.update_column(column_id, body)


@router.delete("/columns/{column_id}", status_code=204)
def remove_column(column_id: UUID, service: TableServiceDep) -> Response:
    """Delete a column that is not used by a relationship."""
    service.delete_column(column_id)
    return Response(status_code=204)


@router.post("/relationships", response_model=RelationshipRead, status_code=201)
def create_relationship(
    body: RelationshipCreate,
    relationships: RelationshipServiceDep,
) -> RelationshipRead:
    """Create a relationship and its column mappings."""
    return relationships.create_relationship(body)


@router.get("/relationships/{relationship_id}", response_model=RelationshipRead)
def read_relationship(relationship_id: UUID, service: RelationshipServiceDep) -> RelationshipRead:
    """Return one relationship."""
    return service.get_relationship(relationship_id)


@router.patch("/relationships/{relationship_id}", response_model=RelationshipRead)
def patch_relationship(
    relationship_id: UUID,
    body: RelationshipUpdate,
    service: RelationshipServiceDep,
) -> RelationshipRead:
    """Patch a relationship."""
    return service.update_relationship(relationship_id, body)


@router.delete("/relationships/{relationship_id}", status_code=204)
def remove_relationship(relationship_id: UUID, service: RelationshipServiceDep) -> Response:
    """Delete a relationship."""
    service.delete_relationship(relationship_id)
    return Response(status_code=204)


@router.get("/tables/{table_id}/relationships", response_model=TableRelationships)
def read_table_relationships(
    table_id: UUID,
    service: RelationshipServiceDep,
) -> TableRelationships:
    """Return incoming and outgoing relationships for a table."""
    return service.list_table_relationships(table_id)


@router.get("/engine-connections", response_model=list[EngineConnectionRead])
def list_engine_connections(service: EngineServiceDep) -> list[EngineConnectionRead]:
    """List Trino and Spark SQL connections."""
    return service.list_engine_connections()


@router.post("/engine-connections", response_model=EngineConnectionRead, status_code=201)
def create_engine_connection(
    body: EngineConnectionCreate,
    service: EngineServiceDep,
) -> EngineConnectionRead:
    """Register an engine connection."""
    return service.create_engine_connection(body)


@router.get("/engine-connections/{engine_connection_id}", response_model=EngineConnectionRead)
def read_engine_connection(
    engine_connection_id: UUID,
    service: EngineServiceDep,
) -> EngineConnectionRead:
    """Return one engine connection."""
    return service.get_engine_connection(engine_connection_id)


@router.patch("/engine-connections/{engine_connection_id}", response_model=EngineConnectionRead)
def patch_engine_connection(
    engine_connection_id: UUID,
    body: EngineConnectionUpdate,
    service: EngineServiceDep,
) -> EngineConnectionRead:
    """Patch an engine connection."""
    return service.update_engine_connection(engine_connection_id, body)


@router.delete("/engine-connections/{engine_connection_id}", status_code=204)
def remove_engine_connection(
    engine_connection_id: UUID,
    service: EngineServiceDep,
) -> Response:
    """Delete an engine connection that has no source bindings."""
    service.delete_engine_connection(engine_connection_id)
    return Response(status_code=204)


@router.get("/source-bindings", response_model=list[SourceBindingRead])
def list_source_bindings(
    service: BindingServiceDep,
    data_source_id: Annotated[UUID | None, Query()] = None,
    engine_connection_id: Annotated[UUID | None, Query()] = None,
) -> list[SourceBindingRead]:
    """List source engine bindings."""
    return service.list_source_bindings(data_source_id, engine_connection_id)


@router.post("/source-bindings", response_model=SourceBindingRead, status_code=201)
def create_source_binding(
    body: SourceBindingCreate,
    service: BindingServiceDep,
) -> SourceBindingRead:
    """Bind a data source to an engine connection."""
    return service.create_source_binding(body)


@router.get("/source-bindings/{source_binding_id}", response_model=SourceBindingRead)
def read_source_binding(
    source_binding_id: UUID,
    service: BindingServiceDep,
) -> SourceBindingRead:
    """Return one source binding."""
    return service.get_source_binding(source_binding_id)


@router.patch("/source-bindings/{source_binding_id}", response_model=SourceBindingRead)
def patch_source_binding(
    source_binding_id: UUID,
    body: SourceBindingUpdate,
    service: BindingServiceDep,
) -> SourceBindingRead:
    """Patch a source binding."""
    return service.update_source_binding(source_binding_id, body)


@router.delete("/source-bindings/{source_binding_id}", status_code=204)
def remove_source_binding(source_binding_id: UUID, service: BindingServiceDep) -> Response:
    """Delete a source binding that has no agent bindings or sync runs."""
    service.delete_source_binding(source_binding_id)
    return Response(status_code=204)


@router.get("/tables/{table_id}/bindings", response_model=list[AgentBindingRead])
def list_agent_bindings(table_id: UUID, service: BindingServiceDep) -> list[AgentBindingRead]:
    """List discovery and query bindings for a table."""
    return service.list_agent_bindings(table_id)


@router.post("/tables/{table_id}/bindings", response_model=AgentBindingRead, status_code=201)
def create_agent_binding(
    table_id: UUID,
    body: AgentBindingCreate,
    service: BindingServiceDep,
) -> AgentBindingRead:
    """Store an explicit engine address for a table."""
    return service.create_agent_binding(table_id, body)


@router.get("/bindings/{binding_id}", response_model=AgentBindingRead)
def read_agent_binding(binding_id: UUID, service: BindingServiceDep) -> AgentBindingRead:
    """Return one agent binding."""
    return service.get_agent_binding(binding_id)


@router.patch("/bindings/{binding_id}", response_model=AgentBindingRead)
def patch_agent_binding(
    binding_id: UUID,
    body: AgentBindingUpdate,
    service: BindingServiceDep,
) -> AgentBindingRead:
    """Patch an agent binding address or selection."""
    return service.update_agent_binding(binding_id, body)


@router.delete("/bindings/{binding_id}", status_code=204)
def remove_agent_binding(binding_id: UUID, service: BindingServiceDep) -> Response:
    """Delete one agent binding."""
    service.delete_agent_binding(binding_id)
    return Response(status_code=204)


@router.get("/sync-runs", response_model=list[SyncRunRead])
def list_sync_runs(
    service: SyncRunServiceDep,
    source_engine_binding_id: Annotated[UUID | None, Query()] = None,
) -> list[SyncRunRead]:
    """List recorded metadata sync runs."""
    return service.list_sync_runs(source_engine_binding_id)


@router.post("/sync-runs", response_model=SyncRunRead, status_code=201)
def create_sync_run(body: SyncRunCreate, service: SyncRunServiceDep) -> SyncRunRead:
    """Record a metadata sync run. This does not contact Trino."""
    return service.create_sync_run(body)


@router.get("/sync-runs/{sync_run_id}", response_model=SyncRunRead)
def read_sync_run(sync_run_id: UUID, service: SyncRunServiceDep) -> SyncRunRead:
    """Return one sync run."""
    return service.get_sync_run(sync_run_id)


@router.patch("/sync-runs/{sync_run_id}", response_model=SyncRunRead)
def patch_sync_run(
    sync_run_id: UUID,
    body: SyncRunUpdate,
    service: SyncRunServiceDep,
) -> SyncRunRead:
    """Update a recorded sync run."""
    return service.update_sync_run(sync_run_id, body)
