"""Structured model call that selects important column relations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.settings import ThinkingLevel

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.context.column_graph import (
    ColumnProposalContext,
    accept_column_relations,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnCandidate,
    ColumnDataProfile,
    ColumnRelation,
    LineageDataset,
    TableAnalysisContext,
    TableRelation,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

_INSTRUCTIONS: str = (
    "You build a column subgraph for one catalog table. "
    "The user message is JSON with the focus table, candidate columns, "
    "existing catalog relationships, column statistics, and lineage table names. "
    "Return only important relations between columns. "
    "Use column ids from the candidate list. "
    "One end of each relation must be a column of the focus table. "
    "REFERENCES means the source column points at the target column, usually a key. "
    "JOINS_ON means the columns are a join key and not a declared foreign key. "
    "DERIVED_FROM means the source column is calculated from the target column. "
    "SAME_MEANING means the columns name the same business fact and are not a join. "
    "Prefer keys, foreign keys, shared identifiers, and clear derivations. "
    "Ignore weak name matches. "
    "context fields hold the business meaning written for the table and columns. "
    "Use them to judge joins and derivations. "
    "reason is one short sentence grounded in the names, types, keys, statistics, or context. "
    "Return no relations when nothing is important. "
    "Do not invent column ids."
)


class ColumnGraphDeps:
    """Candidate columns used to reject invented ids."""

    def __init__(self, focus_table_id: UUID, columns: list[ColumnCandidate]) -> None:
        self.focus_table_id: UUID = focus_table_id
        self.columns: list[ColumnCandidate] = columns


class ColumnRelationSet(BaseModel):
    """Relations the model selected for one table."""

    model_config = ConfigDict(extra="forbid")

    relations: list[ColumnRelation] = Field(default_factory=list, max_length=50)


class _PromptColumn(BaseModel):
    """Compact column facts sent to the model."""

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
    null_ratio: float | None = None
    uniqueness_ratio: float | None = None
    distinct_count: int | None = None


class _PromptMapping(BaseModel):
    """Column pair already stored on a catalog relationship."""

    model_config = ConfigDict(extra="forbid")

    source_column_id: UUID
    target_column_id: UUID


class _PromptRelationship(BaseModel):
    """Catalog relationship reduced to the facts that matter for column links."""

    model_config = ConfigDict(extra="forbid")

    relationship_type: str
    status: str
    source_table_id: UUID
    target_table_id: UUID
    description: str | None
    column_mappings: list[_PromptMapping]


class _ColumnGraphPrompt(BaseModel):
    """User message for one column-subgraph call."""

    model_config = ConfigDict(extra="forbid")

    focus_table_id: UUID
    focus_table_name: str
    focus_table_context: str | None
    columns: list[_PromptColumn]
    existing_relationships: list[_PromptRelationship]
    upstream_tables: list[str]
    downstream_tables: list[str]


class ColumnSubgraphAgent(ProposalAgent[ColumnProposalContext, list[ColumnRelation]]):
    """Asks the model which column relations belong in the knowledge subgraph."""

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        thinking: ThinkingLevel = False,
    ) -> None:
        self._call: ModelCall[ColumnGraphDeps, ColumnRelationSet] = ModelCall(
            _column_agent(model),
            name="column-subgraph",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )

    def propose(self, context: ColumnProposalContext) -> list[ColumnRelation]:
        """Return relations the model selected from ``context``."""
        deps: ColumnGraphDeps = ColumnGraphDeps(context.analysis.table.id, context.columns)
        prompt: str = _prompt(context.analysis, context.columns)
        selected: ColumnRelationSet = self._call.run(prompt, deps)
        return selected.relations


def _column_agent(model: Model) -> Agent[ColumnGraphDeps, ColumnRelationSet]:
    """JSON schema. A tool call lets Qwen think until the answer is empty."""
    agent: Agent[ColumnGraphDeps, ColumnRelationSet] = Agent(
        model,
        deps_type=ColumnGraphDeps,
        output_type=structured_output(model, ColumnRelationSet),
        name="column-subgraph",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    agent.output_validator(_accept_output)
    return agent


def _accept_output(
    ctx: RunContext[ColumnGraphDeps],
    proposed: ColumnRelationSet,
) -> ColumnRelationSet:
    try:
        accept_column_relations(ctx.deps.focus_table_id, ctx.deps.columns, proposed.relations)
    except ValidationAppError as err:
        raise ModelRetry(str(err)) from err
    return proposed


def _prompt(context: TableAnalysisContext, columns: list[ColumnCandidate]) -> str:
    stats: dict[UUID, ColumnDataProfile] = {}
    if context.data_profile is not None:
        profile: ColumnDataProfile
        for profile in context.data_profile.columns:
            stats[profile.column_id] = profile
    prompt_columns: list[_PromptColumn] = []
    column: ColumnCandidate
    for column in columns:
        stat: ColumnDataProfile | None = stats.get(column.column_id)
        prompt_columns.append(
            _PromptColumn(
                column_id=column.column_id,
                table_id=column.table_id,
                table_name=column.table_name,
                name=column.name,
                data_type=column.data_type,
                is_primary_key=column.is_primary_key,
                is_unique=column.is_unique,
                is_foreign_key=column.is_foreign_key,
                on_focus_table=column.on_focus_table,
                description=column.description,
                context=column.context,
                null_ratio=None if stat is None else stat.null_ratio,
                uniqueness_ratio=None if stat is None else stat.uniqueness_ratio,
                distinct_count=None if stat is None else stat.distinct_count,
            )
        )
    links: list[_PromptRelationship] = []
    relation: TableRelation
    for relation in context.existing_relationships:
        links.append(
            _PromptRelationship(
                relationship_type=relation.relationship_type.value,
                status=relation.status.value,
                source_table_id=relation.source_table_id,
                target_table_id=relation.target_table_id,
                description=relation.description,
                column_mappings=[
                    _PromptMapping(
                        source_column_id=mapping.source_column_id,
                        target_column_id=mapping.target_column_id,
                    )
                    for mapping in relation.column_mappings
                ],
            )
        )
    upstream: list[str] = []
    downstream: list[str] = []
    if context.lineage is not None:
        dataset: LineageDataset
        for dataset in context.lineage.upstream_datasets:
            upstream.append(dataset.name)
        for dataset in context.lineage.downstream_datasets:
            downstream.append(dataset.name)
    payload: _ColumnGraphPrompt = _ColumnGraphPrompt(
        focus_table_id=context.table.id,
        focus_table_name=context.table.name,
        focus_table_context=context.table.context,
        columns=prompt_columns,
        existing_relationships=links,
        upstream_tables=upstream,
        downstream_tables=downstream,
    )
    rendered: str = payload.model_dump_json(exclude_none=True)
    return rendered
