"""Context objects for one catalog table or scope. Identifiers are catalog ids."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from intelegent_data_catalog.persistence.enums import (
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


class ScopeSummary(BaseModel):
    """Scope that owns a table."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    space_id: UUID
    name: str
    slug: str
    description: str | None


class SpaceSummary(BaseModel):
    """Space that owns the scope."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    slug: str
    description: str | None


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
    """Table identity plus the scope, space, and tags around it."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    display_name: str | None
    description: str | None
    source_description: str | None
    table_type: TableKind
    status: TableStatus
    data_source_id: UUID
    scope_id: UUID
    space_id: UUID
    physical_catalog: str
    physical_namespace: str
    physical_table: str
    source_key: str
    row_count: int | None
    scope: ScopeSummary
    space: SpaceSummary
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


class ScopeTableSummary(BaseModel):
    """One table inside a scope, with the columns the scope agent may link."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    display_name: str | None
    description: str | None
    status: TableStatus
    row_count: int | None
    columns: list[ColumnMetadata]


class TableKnowledgeInput(BaseModel):
    """Column relations the table agent already stored for one table."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    column_relations: list[ColumnRelation]
    source: Literal["postgres", "neo4j", "both"]


class PreviousScopeFindings(BaseModel):
    """Summary and links from the last scope build. The model may revise them."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None
    links: list[TableLink]
    built_at: datetime | None


class ScopeCatalogContext(BaseModel):
    """Everything known about one scope before the scope agent proposes links."""

    model_config = ConfigDict(extra="forbid")

    scope: ScopeSummary
    space: SpaceSummary
    tables: list[ScopeTableSummary]
    relationships: list[TableRelation]
    table_knowledge: list[TableKnowledgeInput]
    tables_without_context: list[UUID]
    previous: PreviousScopeFindings | None


class ScopeContextOptions(BaseModel):
    """Which parts of scope context to load."""

    model_config = ConfigDict(extra="forbid")

    include_table_knowledge: bool = True
    include_previous: bool = True
    include_summary: bool = True


class ScopeProposal(BaseModel):
    """Links and summary the scope model selected."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=1200)
    links: list[TableLink] = Field(default_factory=list, max_length=60)


class ScopeAnalysisContext(BaseModel):
    """Scope metadata, catalog relationships, accepted links, and the written summary."""

    model_config = ConfigDict(extra="forbid")

    scope: ScopeSummary
    space: SpaceSummary
    tables: list[ScopeTableSummary]
    existing_relationships: list[TableRelation]
    table_links: list[TableLink] = Field(default_factory=list)
    summary: str | None = None
    tables_without_context: list[UUID] = Field(default_factory=list)
    previous_links_kept: int = Field(default=0, ge=0)


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
