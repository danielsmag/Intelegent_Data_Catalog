"""Context objects for catalog tables, columns, assets, and Airflow. Identifiers are catalog ids."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from intelegent_data_catalog.core.settings import StoredEffort, StoredModelId
from intelegent_data_catalog.persistence.enums import (
    AirflowMappingDirection,
    AirflowMappingOrigin,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
    TableKind,
    TableStatus,
)


class DataAccessMode(StrEnum):
    """How much physical data a table agent may request.

    ``PROFILE_ONLY`` is the default. There is no mode for an unbounded read.
    """

    NONE = "NONE"
    METADATA_ONLY = "METADATA_ONLY"
    PROFILE_ONLY = "PROFILE_ONLY"
    SAMPLED = "SAMPLED"


class NeighborDirection(StrEnum):
    """Which way a graph edge points relative to the agent's table."""

    OUTGOING = "outgoing"
    INCOMING = "incoming"


class KnowledgeDefaults(BaseModel):
    """Limits applied to every table agent from one factory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_access_mode: DataAccessMode = DataAccessMode.PROFILE_ONLY
    max_sample_values: int = Field(default=5, ge=0, le=20)
    profile_timeout_seconds: float = Field(default=30.0, gt=0, le=600)


class ProfileOverrides(BaseModel):
    """Partial profiling request. Omitted fields use the factory defaults."""

    model_config = ConfigDict(extra="forbid")

    access_mode: DataAccessMode | None = None
    max_sample_values: int | None = Field(default=None, ge=0, le=20)
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)


class DataProfileOptions(BaseModel):
    """Resolved bounds passed to the profiler. Samples stay off unless mode is ``SAMPLED``."""

    model_config = ConfigDict(extra="forbid")

    access_mode: DataAccessMode = DataAccessMode.PROFILE_ONLY
    max_sample_values: int = Field(default=0, ge=0, le=20)
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)


class TagSummary(BaseModel):
    """Tag attached to a table. The id is the catalog tag id."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str


class ColumnMetadata(BaseModel):
    """Column definition stored in PostgreSQL."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    table_id: UUID
    name: str
    description: str | None
    source_description: str | None
    context: str | None = None
    data_type: str | None
    native_data_type: str | None
    discovered_data_type: str | None
    nullable: bool | None
    ordinal_position: int
    is_primary_key: bool | None
    is_unique: bool | None
    is_foreign_key: bool


class ColumnMappingSummary(BaseModel):
    """One column pair on a catalog relationship."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_column_id: UUID
    target_column_id: UUID
    position: int


class TableRelation(BaseModel):
    """Catalog relationship that starts or ends at a table."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_table_id: UUID
    target_table_id: UUID
    relationship_type: RelationshipType
    origin: RelationshipOrigin
    status: RelationshipStatus
    description: str | None
    column_mappings: list[ColumnMappingSummary]


class TableMetadata(BaseModel):
    """Table identity and the tags attached to it."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    display_name: str | None
    description: str | None
    source_description: str | None
    table_type: TableKind
    status: TableStatus
    data_source_id: UUID
    physical_catalog: str
    physical_namespace: str
    physical_table: str
    source_key: str
    row_count: int | None
    context: str | None = None
    tags: list[TagSummary]


class TableCatalogContext(BaseModel):
    """Everything PostgreSQL knows about one table. No physical sample rows."""

    model_config = ConfigDict(extra="forbid")

    table: TableMetadata
    columns: list[ColumnMetadata]
    relationships: list[TableRelation]


class ColumnDataProfile(BaseModel):
    """Statistics for one column. Samples are optional and are not stored here."""

    model_config = ConfigDict(extra="forbid")

    column_id: UUID
    row_count: int | None = Field(default=None, ge=0)
    null_count: int | None = Field(default=None, ge=0)
    null_ratio: float | None = Field(default=None, ge=0, le=1)
    distinct_count: int | None = Field(default=None, ge=0)
    uniqueness_ratio: float | None = Field(default=None, ge=0, le=1)
    minimum: JsonValue | None = None
    maximum: JsonValue | None = None
    detected_pattern: str | None = None
    sample_values: list[JsonValue] | None = None


class TableDataProfile(BaseModel):
    """Statistics for one table. The access mode records what was allowed."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    access_mode: DataAccessMode
    row_count: int | None = Field(default=None, ge=0)
    columns: list[ColumnDataProfile] = Field(default_factory=list)


class ColumnComparisonProfile(BaseModel):
    """Overlap between two columns. Values themselves are not included."""

    model_config = ConfigDict(extra="forbid")

    source_column_id: UUID
    target_column_id: UUID
    datatype_compatible: bool
    value_overlap_ratio: float | None = Field(default=None, ge=0, le=1)
    source_coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    target_coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    target_uniqueness_ratio: float | None = Field(default=None, ge=0, le=1)


class ColumnRelationKind(StrEnum):
    """Why two columns are linked in the knowledge subgraph."""

    REFERENCES = "REFERENCES"
    JOINS_ON = "JOINS_ON"
    DERIVED_FROM = "DERIVED_FROM"
    SAME_MEANING = "SAME_MEANING"


class ColumnCandidate(BaseModel):
    """One catalog column the model may use when it builds a column subgraph."""

    model_config = ConfigDict(extra="forbid")

    column_id: UUID
    table_id: UUID
    table_name: str
    name: str
    data_type: str | None
    is_primary_key: bool | None
    is_unique: bool | None
    is_foreign_key: bool
    on_focus_table: bool
    description: str | None = None
    context: str | None = None


class ColumnRelation(BaseModel):
    """Important link between two catalog columns. Both ids already exist."""

    model_config = ConfigDict(extra="forbid")

    source_column_id: UUID
    target_column_id: UUID
    kind: ColumnRelationKind
    reason: str = Field(min_length=1, max_length=280)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        cleaned: str = value.strip()
        if cleaned == "":
            raise ValueError("reason is empty")
        return cleaned


class GraphNeighbor(BaseModel):
    """Another catalog table connected by the graph projection."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    name: str
    edge_kind: Literal["RELATED_TO"]
    direction: NeighborDirection


class LineageDataset(BaseModel):
    """A Marquez dataset. A missing catalog id means the dataset is not mapped."""

    model_config = ConfigDict(extra="forbid")

    namespace: str
    name: str
    catalog_table_id: UUID | None
    resolved: bool


class LineageJob(BaseModel):
    """A Marquez job name. A job is a dependency, not a join."""

    model_config = ConfigDict(extra="forbid")

    namespace: str
    name: str


class LineageContext(BaseModel):
    """Upstream and downstream datasets for one catalog table."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    upstream_datasets: list[LineageDataset]
    downstream_datasets: list[LineageDataset]
    producer_jobs: list[LineageJob]
    consumer_jobs: list[LineageJob]


class TableContextOptions(BaseModel):
    """Which parts of table context to load."""

    model_config = ConfigDict(extra="forbid")

    include_data_profile: bool = True
    include_graph: bool = True
    include_lineage: bool = True
    model: StoredModelId = "gpt-4.1"
    effort: StoredEffort = "low"
    profile: ProfileOverrides | None = None


class TableAnalysisContext(BaseModel):
    """Catalog metadata, optional statistics, graph neighbors, lineage, and column links."""

    model_config = ConfigDict(extra="forbid")

    table: TableMetadata
    columns: list[ColumnMetadata]
    data_profile: TableDataProfile | None = None
    existing_relationships: list[TableRelation]
    graph_neighbors: list[GraphNeighbor]
    lineage: LineageContext | None = None
    column_relations: list[ColumnRelation] = Field(default_factory=list)


class TableLinkKind(StrEnum):
    """Why two tables are linked by the scope agent."""

    REFERENCES = "REFERENCES"
    JOINS_ON = "JOINS_ON"


class TableLink(BaseModel):
    """Important link between two tables in one scope. One column pair."""

    model_config = ConfigDict(extra="forbid")

    source_table_id: UUID
    target_table_id: UUID
    source_column_id: UUID
    target_column_id: UUID
    kind: TableLinkKind
    reason: str = Field(min_length=1, max_length=280)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        cleaned: str = value.strip()
        if cleaned == "":
            raise ValueError("reason is empty")
        return cleaned


class PipelineTask(BaseModel):
    """One task on an attached DAG."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    operator_name: str | None = None


class PipelineTableRef(BaseModel):
    """Catalog table a DAG reads, writes, or only mentions."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    name: str
    direction: Literal["READS", "WRITES", "MENTIONS"]


class PipelineInput(BaseModel):
    """One attached Airflow DAG and the catalog tables it reads or writes."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    dag_id: str
    instance_name: str
    instance_context: str | None = None
    instance_summary: str | None = None
    description: str | None = None
    schedule: str | None = None
    summary: str | None = None
    tasks: list[PipelineTask] = Field(default_factory=list)
    reads: list[UUID] = Field(default_factory=list)
    writes: list[UUID] = Field(default_factory=list)
    unknown: list[UUID] = Field(default_factory=list)


class PipelineSummary(BaseModel):
    """DAG facts stored on a scope or space snapshot and shown in the overview."""

    model_config = ConfigDict(extra="forbid")

    dag_id: str
    instance_name: str
    instance_summary: str | None = None
    description: str | None = None
    schedule: str | None = None
    summary: str | None = None
    tasks: list[PipelineTask] = Field(default_factory=list)
    tables: list[PipelineTableRef] = Field(default_factory=list)


def pipeline_summaries(
    pipelines: list[PipelineInput],
    names: dict[UUID, str] | None = None,
) -> list[PipelineSummary]:
    """Keep DAG facts on the snapshot, with table names resolved from the owner."""
    lookup: dict[UUID, str] = {} if names is None else names
    found: list[PipelineSummary] = []
    item: PipelineInput
    for item in pipelines:
        found.append(
            PipelineSummary(
                dag_id=item.dag_id,
                instance_name=item.instance_name,
                instance_summary=item.instance_summary,
                description=item.description,
                schedule=item.schedule,
                summary=item.summary,
                tasks=list(item.tasks),
                tables=_pipeline_tables(item, lookup),
            )
        )
    return found


def pipelines_missing_instance_summary(pipelines: list[PipelineSummary]) -> set[str]:
    """Instance names whose pipelines still have no Airflow summary."""
    missing: set[str] = set()
    item: PipelineSummary
    for item in pipelines:
        if _kept_text(item.instance_summary) is not None:
            continue
        missing.add(item.instance_name)
    return missing


def apply_instance_summaries(
    pipelines: list[PipelineSummary],
    summaries: dict[str, str],
) -> list[PipelineSummary]:
    """Fill pipelines that have no instance summary. A known name supplies the text."""
    updated: list[PipelineSummary] = []
    changed: bool = False
    item: PipelineSummary
    for item in pipelines:
        if _kept_text(item.instance_summary) is not None:
            updated.append(item)
            continue
        found: str | None = _kept_text(summaries.get(item.instance_name))
        if found is None:
            updated.append(item)
            continue
        changed = True
        updated.append(item.model_copy(update={"instance_summary": found}))
    if not changed:
        return pipelines
    return updated


def _kept_text(value: str | None) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text


def _pipeline_tables(item: PipelineInput, names: dict[UUID, str]) -> list[PipelineTableRef]:
    refs: list[PipelineTableRef] = []
    seen: set[UUID] = set()
    table_id: UUID
    for table_id in item.writes:
        _add_pipeline_table(refs, seen, names, table_id, "WRITES")
    for table_id in item.reads:
        _add_pipeline_table(refs, seen, names, table_id, "READS")
    for table_id in item.unknown:
        _add_pipeline_table(refs, seen, names, table_id, "MENTIONS")
    return refs


def _add_pipeline_table(
    refs: list[PipelineTableRef],
    seen: set[UUID],
    names: dict[UUID, str],
    table_id: UUID,
    direction: Literal["READS", "WRITES", "MENTIONS"],
) -> None:
    if table_id in seen:
        return
    name: str | None = names.get(table_id)
    if name is None:
        return
    seen.add(table_id)
    refs.append(PipelineTableRef(table_id=table_id, name=name, direction=direction))


def omitted_pipeline_names(text: str, pipelines: list[PipelineInput]) -> list[str]:
    """DAG ids the text does not name. An empty pipeline list returns none."""
    folded: str = text.casefold()
    missing: list[str] = []
    pipeline: PipelineInput
    for pipeline in pipelines:
        if pipeline.dag_id.casefold() not in folded:
            missing.append(pipeline.dag_id)
    return missing


class DagTaskSummary(BaseModel):
    """One task from a synced DAG, reduced for the Airflow agent."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    operator_name: str | None = None
    downstream_task_ids: list[str] = Field(default_factory=list)


class DagTableRef(BaseModel):
    """Catalog table already linked to a DAG."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    qualified_name: str
    direction: AirflowMappingDirection
    origin: AirflowMappingOrigin


class DagCandidateTable(BaseModel):
    """Catalog table the Airflow agent may attach to a DAG."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    qualified_name: str
    description: str | None = None


class AirflowDagInput(BaseModel):
    """One DAG inside an Airflow instance prompt."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    dag_id: str
    display_name: str | None = None
    description: str | None = None
    schedule: str | None = None
    owners: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    tasks: list[DagTaskSummary] = Field(default_factory=list)
    source_code: str | None = None
    known_tables: list[DagTableRef] = Field(default_factory=list)


class DagTableLink(BaseModel):
    """Table the model says one DAG reads or writes."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    table_id: UUID
    direction: AirflowMappingDirection
    reason: str = Field(min_length=1, max_length=280)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        cleaned: str = value.strip()
        if cleaned == "":
            raise ValueError("reason is empty")
        return cleaned


class DagFinding(BaseModel):
    """Summary and table links for one DAG."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    dag_id: str
    summary: str = Field(default="", max_length=600)
    links: list[DagTableLink] = Field(default_factory=list, max_length=40)


class PreviousAirflowFindings(BaseModel):
    """Findings from the last Airflow build. The model may revise them."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None
    findings: list[DagFinding]
    built_at: datetime | None


class AirflowCatalogContext(BaseModel):
    """One Airflow instance and the DAGs this call should analyze."""

    model_config = ConfigDict(extra="forbid")

    instance_id: UUID
    instance_name: str
    instance_description: str | None = None
    instance_context: str | None = None
    dags: list[AirflowDagInput]
    candidate_tables: list[DagCandidateTable]
    previous: PreviousAirflowFindings | None = None


class AirflowContextOptions(BaseModel):
    """Which parts of an Airflow instance to send to the model."""

    model_config = ConfigDict(extra="forbid")

    include_source: bool = True
    include_previous: bool = True
    include_summary: bool = True
    model: StoredModelId = "gpt-4.1"
    effort: StoredEffort = "low"


class AirflowProposal(BaseModel):
    """Instance summary and one finding per analyzed DAG."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=1200)
    dags: list[DagFinding] = Field(default_factory=list, max_length=40)


class AirflowAnalysisContext(BaseModel):
    """Stored Airflow analysis. Findings are keyed by DAG row."""

    model_config = ConfigDict(extra="forbid")

    instance_id: UUID
    instance_name: str
    summary: str | None = None
    findings: list[DagFinding] = Field(default_factory=list)
    dags_analyzed: int = Field(default=0, ge=0)
    previous_links_kept: int = Field(default=0, ge=0)


ContextSubjectKind = Literal[
    "table",
    "table_columns",
    "column",
    "airflow",
    "asset",
]

_CONTEXT_LIMIT: int = 1200


def _context_text(value: object) -> str:
    """Keep generated context. A long answer is cut to the stored limit."""
    if not isinstance(value, str):
        raise ValueError("context is empty")
    cleaned: str = value.strip()
    if cleaned == "":
        raise ValueError("context is empty")
    return cleaned[:_CONTEXT_LIMIT]


class ContextGenerationOptions(BaseModel):
    """Which entity a background context job writes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ContextSubjectKind
    model: StoredModelId = "gpt-4.1"
    effort: StoredEffort = "low"


class _ContextText(BaseModel):
    """Shared rules for generated business context."""

    model_config = ConfigDict(extra="ignore")

    context: str

    @field_validator("context", mode="before")
    @classmethod
    def _clean_context(cls, value: object) -> str:
        return _context_text(value)


class ColumnContextProposal(_ContextText):
    """Business meaning the model wrote for one column."""


class TableContextProposal(_ContextText):
    """Business meaning the model wrote for one table."""


class ColumnContextItem(BaseModel):
    """One column's context inside a whole-table generation."""

    model_config = ConfigDict(extra="ignore")

    column_id: UUID
    context: str

    @field_validator("context", mode="before")
    @classmethod
    def _clean_context(cls, value: object) -> str:
        return _context_text(value)


class TableColumnsContextProposal(BaseModel):
    """Context for every column the model was shown."""

    model_config = ConfigDict(extra="ignore")

    columns: list[ColumnContextItem] = Field(default_factory=list, max_length=40)


class AirflowContextProposal(_ContextText):
    """Business meaning the model wrote for one Airflow instance."""


class TableContextInput(BaseModel):
    """One table plus the saved analysis, when a build has run."""

    model_config = ConfigDict(extra="forbid")

    table: TableCatalogContext
    saved: TableAnalysisContext | None = None
    subgraph: list[ColumnRelation] = Field(default_factory=list)


class TableColumnsContextInput(BaseModel):
    """A batch of columns from one table plus the saved analysis."""

    model_config = ConfigDict(extra="forbid")

    table: TableCatalogContext
    columns: list[ColumnMetadata]
    saved: TableAnalysisContext | None = None
    subgraph: list[ColumnRelation] = Field(default_factory=list)


class ColumnContextInput(BaseModel):
    """One column plus its table and the saved analysis."""

    model_config = ConfigDict(extra="forbid")

    table: TableCatalogContext
    column: ColumnMetadata
    saved: TableAnalysisContext | None = None
    subgraph: list[ColumnRelation] = Field(default_factory=list)


class GraphPublishResult(BaseModel):
    """Counts written when the Neo4j projection is replaced."""

    model_config = ConfigDict(extra="forbid")

    nodes: int = Field(ge=0)
    edges: int = Field(ge=0)


class DatasetIdentity(BaseModel):
    """Catalog table mapped to one Marquez dataset."""

    model_config = ConfigDict(extra="forbid")

    catalog_table_id: UUID
    namespace: str
    name: str
