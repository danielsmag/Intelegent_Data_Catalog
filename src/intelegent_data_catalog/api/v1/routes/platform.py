"""Discovery, graph, search, tags, and Spark-from-catalog routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.api.dependencies import KnowledgeBuildDep, PlatformDep, ScopeBuildDep
from intelegent_data_catalog.catalog.models import QueryResult
from intelegent_data_catalog.exceptions import ConflictError, EntityNotFoundError
from intelegent_data_catalog.graph.document import GraphView
from intelegent_data_catalog.jobs.models import KnowledgeBuildJobRead, ScopeBuildJobRead
from intelegent_data_catalog.knowledge.models import (
    ScopeAnalysisContext,
    ScopeContextOptions,
    TableAnalysisContext,
    TableContextOptions,
)
from intelegent_data_catalog.query.spark_bridge import (
    NOT_CONFIGURED,
    SparkQueryPreview,
    spark_preview,
)
from intelegent_data_catalog.registry.annotations import (
    MarquezMappingRead,
    MarquezMappingWrite,
    SearchResponse,
    TagRead,
    TagWrite,
)
from intelegent_data_catalog.registry.schemas import SyncRunRead

router: APIRouter = APIRouter()


class SparkQueryResponse(BaseModel):
    """Spark editor availability for one catalog table."""

    model_config = ConfigDict(extra="forbid")

    available: bool
    message: str | None
    sql: str | None
    engine_ref_key: str | None


class KnowledgeContextRequest(BaseModel):
    """Which slices ``build_context`` should load. Data profiling stays off."""

    model_config = ConfigDict(extra="forbid")

    include_data_profile: bool = False
    include_graph: bool = True
    include_lineage: bool = True


class ScopeContextRequest(BaseModel):
    """Which slices a scope build should load."""

    model_config = ConfigDict(extra="forbid")

    include_table_knowledge: bool = True
    include_previous: bool = True
    include_summary: bool = True


class GraphRebuildResponse(BaseModel):
    """Counts from a projection rebuild."""

    model_config = ConfigDict(extra="forbid")

    nodes: int
    edges: int


@router.post("/source-bindings/{source_binding_id}/sync", response_model=SyncRunRead)
def sync_source_binding(source_binding_id: UUID, platform: PlatformDep) -> SyncRunRead:
    """Discover metadata through Trino and upsert the catalog."""
    return platform.sync.run(source_binding_id)


@router.get("/search", response_model=SearchResponse)
def search_catalog(
    platform: PlatformDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
) -> SearchResponse:
    """Search PostgreSQL catalog text."""
    return platform.annotations.search(q)


@router.get("/tags", response_model=list[TagRead])
def list_tags(platform: PlatformDep) -> list[TagRead]:
    """List tags."""
    return platform.annotations.list_tags()


@router.post("/tags", response_model=TagRead, status_code=201)
def create_tag(body: TagWrite, platform: PlatformDep) -> TagRead:
    """Create a tag."""
    return platform.annotations.create_tag(body)


@router.get("/tables/{table_id}/tags", response_model=list[TagRead])
def list_table_tags(table_id: UUID, platform: PlatformDep) -> list[TagRead]:
    """List tags on a table."""
    return platform.annotations.list_table_tags(table_id)


@router.post("/tables/{table_id}/tags/{tag_id}", response_model=TagRead, status_code=201)
def attach_tag(table_id: UUID, tag_id: UUID, platform: PlatformDep) -> TagRead:
    """Attach a user tag to a table."""
    return platform.annotations.attach_tag(table_id, tag_id)


@router.post("/marquez-mappings", response_model=MarquezMappingRead, status_code=201)
def create_marquez_mapping(body: MarquezMappingWrite, platform: PlatformDep) -> MarquezMappingRead:
    """Map a catalog table to a Marquez dataset."""
    return platform.annotations.create_mapping(body)


@router.get("/tables/{table_id}/spark-query", response_model=SparkQueryResponse)
def read_spark_query(table_id: UUID, platform: PlatformDep) -> SparkQueryResponse:
    """Return the Spark SELECT for a table, or an explicit unavailable state."""
    preview: SparkQueryPreview = spark_preview(
        platform.registry.list_asset_bindings(table_id),
        platform.sample_limit,
    )
    return SparkQueryResponse(
        available=preview.available,
        message=preview.message,
        sql=preview.sql,
        engine_ref_key=preview.engine_ref_key,
    )


@router.post("/tables/{table_id}/spark-query", response_model=QueryResult)
def run_spark_query(table_id: UUID, platform: PlatformDep) -> QueryResult:
    """Run the bounded Spark SELECT. A missing binding does not fall back to Trino."""
    preview: SparkQueryPreview = spark_preview(
        platform.registry.list_asset_bindings(table_id),
        platform.sample_limit,
    )
    if not preview.available or preview.sql is None:
        raise ConflictError(NOT_CONFIGURED)
    return platform.query.query(preview.sql)


@router.get("/tables/{table_id}/relationships/graph", response_model=GraphView)
def read_relationship_graph(
    table_id: UUID,
    platform: PlatformDep,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
) -> GraphView:
    """Return catalog relationships around a table."""
    return platform.graph.relationships(table_id, depth)


@router.get("/tables/{table_id}/lineage", response_model=GraphView)
def read_lineage(
    table_id: UUID,
    platform: PlatformDep,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
    direction: Annotated[str, Query()] = "both",
) -> GraphView:
    """Return operational lineage around a table."""
    return platform.graph.lineage(table_id, depth, direction)


@router.get("/tables/{table_id}/knowledge-context", response_model=TableAnalysisContext)
def read_knowledge_context(table_id: UUID, platform: PlatformDep) -> TableAnalysisContext:
    """Return the context last built for this table."""
    return platform.read_knowledge_context(table_id)


@router.post(
    "/tables/{table_id}/knowledge-context",
    response_model=KnowledgeBuildJobRead,
    status_code=202,
)
def build_knowledge_context(
    table_id: UUID,
    platform: PlatformDep,
    builds: KnowledgeBuildDep,
    body: KnowledgeContextRequest | None = None,
) -> KnowledgeBuildJobRead:
    """Start a context build and return before the model finishes.

    When Neo4j is configured, the background build asks the model for a column subgraph.
    PostgreSQL catalog rows stay unchanged.
    """
    table_name: str = platform.registry.get_table(table_id).name
    selected: KnowledgeContextRequest = body if body is not None else KnowledgeContextRequest()
    options: TableContextOptions = TableContextOptions(
        include_data_profile=selected.include_data_profile,
        include_graph=selected.include_graph,
        include_lineage=selected.include_lineage,
    )
    return builds.start(table_id, table_name, options)


@router.get("/knowledge-builds/{job_id}", response_model=KnowledgeBuildJobRead)
def read_knowledge_build(job_id: UUID, builds: KnowledgeBuildDep) -> KnowledgeBuildJobRead:
    """Return one background context build."""
    job: KnowledgeBuildJobRead | None = builds.get(job_id)
    if job is None:
        raise EntityNotFoundError("knowledge build", str(job_id))
    return job


@router.get(
    "/tables/{table_id}/knowledge-context/build",
    response_model=KnowledgeBuildJobRead,
)
def read_latest_knowledge_build(
    table_id: UUID,
    builds: KnowledgeBuildDep,
) -> KnowledgeBuildJobRead:
    """Return the newest context build for a table."""
    job: KnowledgeBuildJobRead | None = builds.latest(table_id)
    if job is None:
        raise EntityNotFoundError("knowledge build", str(table_id))
    return job


@router.get("/tables/{table_id}/knowledge-graph", response_model=GraphView)
def read_knowledge_graph(table_id: UUID, platform: PlatformDep) -> GraphView:
    """Return the Neo4j column subgraph owned by this table."""
    return platform.graph.knowledge_subgraph(table_id)


@router.get("/scopes/{scope_id}/graph", response_model=GraphView)
def read_scope_graph(scope_id: UUID, platform: PlatformDep) -> GraphView:
    """Return tables in a scope and the relationships between them."""
    return platform.graph.scope_overview(scope_id)


@router.get("/scopes/{scope_id}/knowledge-graph", response_model=GraphView)
def read_scope_knowledge_graph(scope_id: UUID, platform: PlatformDep) -> GraphView:
    """Return the Neo4j table subgraph owned by this scope."""
    return platform.graph.scope_knowledge_subgraph(scope_id)


@router.get("/scopes/{scope_id}/knowledge-context", response_model=ScopeAnalysisContext)
def read_scope_knowledge_context(scope_id: UUID, platform: PlatformDep) -> ScopeAnalysisContext:
    """Return the context last built for this scope."""
    return platform.read_scope_context(scope_id)


@router.post(
    "/scopes/{scope_id}/knowledge-context",
    response_model=ScopeBuildJobRead,
    status_code=202,
)
def build_scope_knowledge_context(
    scope_id: UUID,
    platform: PlatformDep,
    builds: ScopeBuildDep,
    body: ScopeContextRequest | None = None,
) -> ScopeBuildJobRead:
    """Start a scope build and return before the model finishes.

    Accepted links replace prior LLM relationships for tables in this scope.
    """
    scope_name: str = platform.registry.get_scope(scope_id).name
    selected: ScopeContextRequest = body if body is not None else ScopeContextRequest()
    options: ScopeContextOptions = ScopeContextOptions(
        include_table_knowledge=selected.include_table_knowledge,
        include_previous=selected.include_previous,
        include_summary=selected.include_summary,
    )
    return builds.start(scope_id, scope_name, options)


@router.get("/scope-builds/{job_id}", response_model=ScopeBuildJobRead)
def read_scope_build(job_id: UUID, builds: ScopeBuildDep) -> ScopeBuildJobRead:
    """Return one background scope build."""
    job: ScopeBuildJobRead | None = builds.get(job_id)
    if job is None:
        raise EntityNotFoundError("scope build", str(job_id))
    return job


@router.get(
    "/scopes/{scope_id}/knowledge-context/build",
    response_model=ScopeBuildJobRead,
)
def read_latest_scope_build(scope_id: UUID, builds: ScopeBuildDep) -> ScopeBuildJobRead:
    """Return the newest context build for a scope."""
    job: ScopeBuildJobRead | None = builds.latest(scope_id)
    if job is None:
        raise EntityNotFoundError("scope build", str(scope_id))
    return job


@router.get("/tables/{table_id}/context-graph", response_model=GraphView)
def read_context_graph(
    table_id: UUID,
    platform: PlatformDep,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
) -> GraphView:
    """Return hierarchy, relationships, and lineage around a table."""
    return platform.graph.context(table_id, depth)


@router.get("/tables/{table_id}/graph", response_model=GraphView)
def read_graph(
    table_id: UUID,
    platform: PlatformDep,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
) -> GraphView:
    """Return a bounded catalog graph around a table."""
    return platform.graph.context(table_id, depth)


@router.post("/graph/rebuild", response_model=GraphRebuildResponse)
def rebuild_graph(platform: PlatformDep) -> GraphRebuildResponse:
    """Rebuild the Neo4j projection from PostgreSQL and Marquez."""
    counts: dict[str, int] = platform.graph.rebuild()
    return GraphRebuildResponse(nodes=counts["nodes"], edges=counts["edges"])
