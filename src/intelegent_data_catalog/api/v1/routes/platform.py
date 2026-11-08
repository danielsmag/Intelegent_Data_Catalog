"""Discovery, graph, search, tags, and Spark-from-catalog routes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.dependencies import (
    AccessDep,
    CatalogJobsDep,
    EntityConfigurationDep,
    PlatformDep,
    SyncScheduleDep,
    get_container,
)
from intelegent_data_catalog.catalog.models import QueryResult
from intelegent_data_catalog.core.settings import CelerySettings, ModelId, ReasoningEffort
from intelegent_data_catalog.exceptions import (
    AuthorizationError,
    ConflictError,
    EntityNotFoundError,
)
from intelegent_data_catalog.graph.document import GraphView
from intelegent_data_catalog.jobs.cancel import CancelSignal, RedisCancelSignal
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobStore
from intelegent_data_catalog.jobs.models import (
    AirflowBuildJobRead,
    CatalogJobCancelRead,
    ContextGenerationJobRead,
    JobApprovalRead,
    JobApprovalWrite,
    KnowledgeBuildJobRead,
)
from intelegent_data_catalog.jobs.trace import (
    BuildTraceEvent,
    BuildTraceJob,
    BuildTraceList,
    RedisBuildTrace,
    trace_status_for_run,
)
from intelegent_data_catalog.knowledge.models import (
    AirflowAnalysisContext,
    AirflowContextOptions,
    ContextGenerationOptions,
    ContextSubjectKind,
    TableAnalysisContext,
    TableContextOptions,
)
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import RefreshSubjectKind
from intelegent_data_catalog.persistence.models import AssetRunRow, CatalogJobRow
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
from intelegent_data_catalog.registry.schemas import CatalogTableRead, ColumnRead, SyncRunRead

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






class AirflowContextRequest(BaseModel):
    """Which slices an Airflow instance build should load."""

    model_config = ConfigDict(extra="forbid")

    include_source: bool = True
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
        platform.bindings.list_agent_bindings(table_id),
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
        platform.bindings.list_agent_bindings(table_id),
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
    builds: CatalogJobsDep,
    configurations: EntityConfigurationDep,
    body: KnowledgeContextRequest | None = None,
) -> KnowledgeBuildJobRead:
    """Start a context build and return before the model finishes.

    When Neo4j is configured, the background build asks the model for a column subgraph.
    PostgreSQL catalog rows stay unchanged. A table that has an agent is built there.
    """
    table: CatalogTableRead = platform.tables.get_table(table_id)
    if table.agent_id is not None:
        raise ConflictError(f"table context is built on agent {table.agent_id}")
    table_name: str = table.name
    selected: KnowledgeContextRequest = body if body is not None else KnowledgeContextRequest()
    options: TableContextOptions = TableContextOptions(
        include_data_profile=selected.include_data_profile,
        include_graph=selected.include_graph,
        include_lineage=selected.include_lineage,
        model=configurations.saved_model(RefreshSubjectKind.TABLE, table_id),
        effort=configurations.saved_effort(RefreshSubjectKind.TABLE, table_id),
    )
    return builds.start_knowledge(table_id, table_name, options)


@router.get("/knowledge-builds/{job_id}", response_model=KnowledgeBuildJobRead)
def read_knowledge_build(job_id: UUID, builds: CatalogJobsDep) -> KnowledgeBuildJobRead:
    """Return one background context build."""
    job: KnowledgeBuildJobRead | None = builds.get_knowledge(job_id)
    if job is None:
        raise EntityNotFoundError("knowledge build", str(job_id))
    return job


@router.get(
    "/tables/{table_id}/knowledge-context/build",
    response_model=KnowledgeBuildJobRead,
)
def read_latest_knowledge_build(
    table_id: UUID,
    builds: CatalogJobsDep,
) -> KnowledgeBuildJobRead:
    """Return the newest context build for a table."""
    job: KnowledgeBuildJobRead | None = builds.latest_knowledge(table_id)
    if job is None:
        raise EntityNotFoundError("knowledge build", str(table_id))
    return job


@router.get("/tables/{table_id}/knowledge-graph", response_model=GraphView)
def read_knowledge_graph(table_id: UUID, platform: PlatformDep) -> GraphView:
    """Return the Neo4j column subgraph owned by this table."""
    return platform.graph.knowledge_subgraph(table_id)






















@router.post("/catalog-jobs/{job_id}/cancel", response_model=CatalogJobCancelRead)
def cancel_catalog_job(
    job_id: UUID,
    builds: CatalogJobsDep,
    container: Annotated[AppContainer, Depends(get_container)],
) -> CatalogJobCancelRead:
    """Stop a running build. A live worker exits. A stuck row is released."""
    celery_settings: CelerySettings = container.settings().celery
    signal: CancelSignal = RedisCancelSignal.from_url(celery_settings.broker_url)
    ttl_seconds: int = max(int(celery_settings.visibility_timeout_seconds), 1)
    cancelled: CatalogJobCancelRead | None = builds.cancel(
        job_id,
        signal,
        ttl_seconds,
        trace=RedisBuildTrace.from_url(celery_settings.broker_url),
    )
    if cancelled is None:
        raise EntityNotFoundError("catalog job", str(job_id))
    return cancelled


@router.get("/catalog-jobs/{job_id}/approval", response_model=JobApprovalRead)
def read_catalog_job_approval(job_id: UUID, builds: CatalogJobsDep) -> JobApprovalRead:
    """Return the step a paused build is waiting on."""
    pending: JobApprovalRead | None = builds.approval(job_id)
    if pending is None:
        raise EntityNotFoundError("catalog job", str(job_id))
    return pending


@router.post("/catalog-jobs/{job_id}/approval", response_model=JobApprovalRead)
def submit_catalog_job_approval(
    job_id: UUID,
    body: JobApprovalWrite,
    builds: CatalogJobsDep,
) -> JobApprovalRead:
    """Approve or reject the paused step and resume the worker."""
    decided: JobApprovalRead | None = builds.decide(job_id, body.decision, body.output)
    if decided is None:
        raise EntityNotFoundError("catalog job", str(job_id))
    return decided












@router.post(
    "/tables/{table_id}/context/generate",
    response_model=ContextGenerationJobRead,
    status_code=202,
)
def generate_table_context(
    table_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    configurations: EntityConfigurationDep,
) -> ContextGenerationJobRead:
    """Start a table context write and return before the model finishes."""
    table_name: str = platform.tables.get_table(table_id).name
    return _start_context(
        builds,
        table_id,
        table_name,
        "table",
        configurations.saved_model(RefreshSubjectKind.TABLE, table_id),
        configurations.saved_effort(RefreshSubjectKind.TABLE, table_id),
    )


@router.post(
    "/tables/{table_id}/columns/context/generate",
    response_model=ContextGenerationJobRead,
    status_code=202,
)
def generate_table_column_contexts(
    table_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    configurations: EntityConfigurationDep,
) -> ContextGenerationJobRead:
    """Start context writes for every column on the table."""
    table_name: str = platform.tables.get_table(table_id).name
    return _start_context(
        builds,
        table_id,
        f"{table_name} columns",
        "table_columns",
        configurations.saved_model(RefreshSubjectKind.TABLE, table_id),
        configurations.saved_effort(RefreshSubjectKind.TABLE, table_id),
    )


@router.post(
    "/columns/{column_id}/context/generate",
    response_model=ContextGenerationJobRead,
    status_code=202,
)
def generate_column_context(
    column_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    configurations: EntityConfigurationDep,
) -> ContextGenerationJobRead:
    """Start a column context write and return before the model finishes."""
    column: ColumnRead = platform.tables.get_column(column_id)
    return _start_context(
        builds,
        column_id,
        column.name,
        "column",
        configurations.saved_model(RefreshSubjectKind.TABLE, column.table_id),
        configurations.saved_effort(RefreshSubjectKind.TABLE, column.table_id),
    )


@router.get("/context-generations/{job_id}", response_model=ContextGenerationJobRead)
def read_context_generation(job_id: UUID, builds: CatalogJobsDep) -> ContextGenerationJobRead:
    """Return one background context generation."""
    job: ContextGenerationJobRead | None = builds.get_context(job_id)
    if job is None:
        raise EntityNotFoundError("context generation", str(job_id))
    return job




@router.get("/tables/{table_id}/context/generation", response_model=ContextGenerationJobRead)
def read_latest_table_context_generation(
    table_id: UUID,
    builds: CatalogJobsDep,
    lane: Annotated[Literal["table", "table_columns"], Query()],
) -> ContextGenerationJobRead:
    """Return the newest table or all-columns context generation."""
    return _latest_context(builds, table_id, lane)


@router.get("/columns/{column_id}/context/generation", response_model=ContextGenerationJobRead)
def read_latest_column_context_generation(
    column_id: UUID,
    builds: CatalogJobsDep,
) -> ContextGenerationJobRead:
    """Return the newest column context generation."""
    return _latest_context(builds, column_id, "column")


@router.get(
    "/airflow/instances/{instance_id}/knowledge-context",
    response_model=AirflowAnalysisContext,
)
def read_airflow_knowledge_context(
    instance_id: UUID,
    platform: PlatformDep,
) -> AirflowAnalysisContext:
    """Return the context last built for this Airflow instance."""
    return platform.read_airflow_context(instance_id)


@router.post(
    "/airflow/instances/{instance_id}/knowledge-context",
    response_model=AirflowBuildJobRead,
    status_code=202,
)
def build_airflow_knowledge_context(
    instance_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    schedules: SyncScheduleDep,
    body: AirflowContextRequest | None = None,
) -> AirflowBuildJobRead:
    """Start an Airflow build and return before the model finishes.

    One model call covers the synced DAGs of this instance.
    """
    instance_name: str = platform.airflow_instance_name(instance_id)
    selected: AirflowContextRequest = body if body is not None else AirflowContextRequest()
    options: AirflowContextOptions = AirflowContextOptions(
        include_source=selected.include_source,
        include_previous=selected.include_previous,
        include_summary=selected.include_summary,
        model=schedules.saved_model(instance_id),
        effort=schedules.saved_effort(instance_id),
    )
    return builds.start_airflow(instance_id, instance_name, options)


@router.get("/airflow-builds/{job_id}", response_model=AirflowBuildJobRead)
def read_airflow_build(job_id: UUID, builds: CatalogJobsDep) -> AirflowBuildJobRead:
    """Return one background Airflow build."""
    job: AirflowBuildJobRead | None = builds.get_airflow(job_id)
    if job is None:
        raise EntityNotFoundError("airflow build", str(job_id))
    return job


@router.get(
    "/airflow/instances/{instance_id}/knowledge-context/build",
    response_model=AirflowBuildJobRead,
)
def read_latest_airflow_build(instance_id: UUID, builds: CatalogJobsDep) -> AirflowBuildJobRead:
    """Return the newest context build for an Airflow instance."""
    job: AirflowBuildJobRead | None = builds.latest_airflow(instance_id)
    if job is None:
        raise EntityNotFoundError("airflow build", str(instance_id))
    return job


@router.post(
    "/airflow/instances/{instance_id}/context/generate",
    response_model=ContextGenerationJobRead,
    status_code=202,
)
def generate_airflow_context(
    instance_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    schedules: SyncScheduleDep,
) -> ContextGenerationJobRead:
    """Start an Airflow context write and return before the model finishes."""
    instance_name: str = platform.airflow_instance_name(instance_id)
    return _start_context(
        builds,
        instance_id,
        instance_name,
        "airflow",
        schedules.saved_model(instance_id),
        schedules.saved_effort(instance_id),
    )


@router.get(
    "/airflow/instances/{instance_id}/context/generation",
    response_model=ContextGenerationJobRead,
)
def read_latest_airflow_context_generation(
    instance_id: UUID,
    builds: CatalogJobsDep,
) -> ContextGenerationJobRead:
    """Return the newest Airflow context generation."""
    return _latest_context(builds, instance_id, "airflow")


@router.get("/build-traces", response_model=BuildTraceList)
def read_build_traces(
    container: Annotated[AppContainer, Depends(get_container)],
    after: Annotated[str | None, Query()] = None,
    jobs: Annotated[str | None, Query()] = None,
) -> BuildTraceList:
    """Return context-build steps newer than the cursor, plus job row status.

    The row status closes a card whose terminal step never reached Redis.
    """
    broker_url: str = container.settings().celery.broker_url
    sink: RedisBuildTrace = RedisBuildTrace.from_url(broker_url)
    events: list[BuildTraceEvent] = sink.read(after)
    cursor: str | None = events[-1].id if len(events) > 0 else after
    wanted: set[UUID] = {event.job_id for event in events}
    wanted.update(_trace_job_ids(jobs))
    database: CatalogDatabase = container.database()
    loaded: list[CatalogJobRow] = CatalogJobStore(database).statuses(wanted)
    rows: list[CatalogJobRow] = loaded
    states: list[BuildTraceJob] = []
    seen: set[UUID] = set()
    row: CatalogJobRow
    for row in rows:
        status: Literal["running", "succeeded", "failed"] | None = _trace_status(row.status)
        if status is None:
            continue
        seen.add(row.id)
        states.append(
            BuildTraceJob(
                job_id=row.id,
                status=status,
                error=row.error,
                finished_at=row.finished_at,
            ),
        )
    states.extend(_asset_trace_jobs(database, wanted - seen))
    return BuildTraceList(events=events, cursor=cursor, jobs=states)


_TRACE_JOB_LIMIT: int = 80


def _trace_job_ids(raw: str | None) -> set[UUID]:
    """Parse the job ids the panel still shows as running."""
    found: set[UUID] = set()
    if raw is None or raw.strip() == "":
        return found
    part: str
    for part in raw.split(","):
        token: str = part.strip()
        if token == "":
            continue
        try:
            found.add(UUID(token))
        except ValueError:
            continue
        if len(found) >= _TRACE_JOB_LIMIT:
            break
    return found


def _trace_status(value: str) -> Literal["running", "succeeded", "failed"] | None:
    if value == "running" or value == "succeeded" or value == "failed":
        return value
    return None


def _asset_trace_jobs(database: CatalogDatabase, run_ids: set[UUID]) -> list[BuildTraceJob]:
    """Close asset cards from the run row when the terminal step never reached Redis."""
    if len(run_ids) == 0:
        return []
    session: Session = database.session()
    try:
        rows: list[AssetRunRow] = list(
            session.scalars(select(AssetRunRow).where(AssetRunRow.id.in_(run_ids))),
        )
    finally:
        session.close()
    states: list[BuildTraceJob] = []
    run: AssetRunRow
    for run in rows:
        status: Literal["running", "succeeded", "failed"] | None = trace_status_for_run(run.status)
        if status is None:
            continue
        states.append(
            BuildTraceJob(
                job_id=run.id,
                status=status,
                error=run.error,
                finished_at=run.finished_at,
            ),
        )
    return states


def _start_context(
    builds: CatalogJobsDep,
    subject_id: UUID,
    subject_name: str,
    kind: ContextSubjectKind,
    model: ModelId,
    effort: ReasoningEffort,
) -> ContextGenerationJobRead:
    return builds.start_context(
        subject_id,
        subject_name,
        ContextGenerationOptions(kind=kind, model=model, effort=effort),
        lane=kind,
    )


def _latest_context(
    builds: CatalogJobsDep,
    subject_id: UUID,
    lane: ContextSubjectKind,
) -> ContextGenerationJobRead:
    job: ContextGenerationJobRead | None = builds.latest_context(subject_id, lane)
    if job is None:
        raise EntityNotFoundError("context generation", str(subject_id))
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
def rebuild_graph(platform: PlatformDep, access: AccessDep) -> GraphRebuildResponse:
    """Rebuild the Neo4j projection from PostgreSQL and Marquez."""
    if not access.principal.is_admin:
        raise AuthorizationError("platform admin required")
    counts: dict[str, int] = platform.graph.rebuild()
    return GraphRebuildResponse(nodes=counts["nodes"], edges=counts["edges"])
