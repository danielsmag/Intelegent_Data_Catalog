"""Structured model call that selects cross-table links and a scope summary."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.settings import ThinkingLevel

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.context.scope_graph import accept_table_links
from intelegent_data_catalog.knowledge.models import (
    ColumnMetadata,
    PipelineInput,
    PreviousScopeFindings,
    ScopeCatalogContext,
    ScopeProposal,
    ScopeTableSummary,
    TableKnowledgeInput,
    TableLink,
    TableRelation,
    omitted_pipeline_names,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

_INSTRUCTIONS: str = (
    "You build a scope overview for one business scope. "
    "The user message is JSON with the scope, its tables and columns, "
    "existing catalog relationships, column relations already found by each table agent, "
    "and previous_findings from the last scope build when one exists. "
    "Return a short summary and the important links between tables in this scope. "
    "Each link uses one column pair. "
    "REFERENCES means the source column points at the target column, usually a key. "
    "JOINS_ON means the columns are a join key and not a declared foreign key. "
    "Prefer keys, foreign keys, shared identifiers, and findings that cross tables. "
    "Ignore weak name matches. "
    "context fields hold the business meaning written for the scope, tables, and columns. "
    "Use them to judge which links matter. "
    "Do not repeat a column pair that is already an existing catalog relationship. "
    "Treat previous_findings as a draft: keep a justified link, drop the rest, or add one. "
    "summary is two to four sentences on what this scope describes and how the tables join. "
    "Mention a table without context only when that gap matters. "
    "pipelines lists Airflow DAGs attached to this scope and the tables each DAG reads or writes. "
    "description is the DAG text. "
    "reads, writes, and unknown are table ids. "
    "unknown means the DAG mentions the table and the direction is not known. "
    "instance_context is the business meaning already written for that Airflow instance. "
    "Tables written by one pipeline from the same inputs usually share keys. "
    "Use the description and those lists to find joins and to say what each pipeline does. "
    "Do not add a link only because a DAG mentions one table. "
    "When pipelines is not empty, the summary must name every dag_id "
    "and say what that pipeline does. "
    "reason is one short sentence grounded in the names, types, keys, or findings. "
    "Return no links when nothing is important. "
    "Do not invent table ids or column ids."
)


class _PromptColumn(BaseModel):
    """Compact column facts sent to the model."""

    model_config = ConfigDict(extra="forbid")

    column_id: UUID
    name: str
    data_type: str | None
    is_primary_key: bool | None
    is_unique: bool | None
    is_foreign_key: bool
    description: str | None = None
    context: str | None = None


class _PromptTable(BaseModel):
    """One scope table sent to the model."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    name: str
    description: str | None
    context: str | None
    row_count: int | None
    has_table_context: bool
    columns: list[_PromptColumn]


class _PromptMapping(BaseModel):
    """Column pair already stored on a catalog relationship."""

    model_config = ConfigDict(extra="forbid")

    source_column_id: UUID
    target_column_id: UUID


class _PromptRelationship(BaseModel):
    """Catalog relationship reduced to the facts that matter for table links."""

    model_config = ConfigDict(extra="forbid")

    relationship_type: str
    origin: str
    source_table_id: UUID
    target_table_id: UUID
    description: str | None
    column_mappings: list[_PromptMapping]


class _PromptColumnRelation(BaseModel):
    """Column relation already stored by a table agent."""

    model_config = ConfigDict(extra="forbid")

    source_column_id: UUID
    target_column_id: UUID
    kind: str
    reason: str


class _PromptTableKnowledge(BaseModel):
    """Merged table-agent findings for one table."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    source: str
    column_relations: list[_PromptColumnRelation]


class _PromptLink(BaseModel):
    """One link from the previous scope build."""

    model_config = ConfigDict(extra="forbid")

    source_table_id: UUID
    target_table_id: UUID
    source_column_id: UUID
    target_column_id: UUID
    kind: str
    reason: str


class _PromptPrevious(BaseModel):
    """Draft the model may keep, drop, or extend."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None
    links: list[_PromptLink]


class _PromptPipeline(BaseModel):
    """One attached DAG sent to the scope model."""

    model_config = ConfigDict(extra="forbid")

    dag_id: str
    instance_name: str
    instance_context: str | None
    description: str | None
    summary: str | None
    schedule: str | None
    reads: list[UUID]
    writes: list[UUID]
    unknown: list[UUID]


class _ScopeGraphPrompt(BaseModel):
    """User message for one scope-subgraph call."""

    model_config = ConfigDict(extra="forbid")

    scope_id: UUID
    scope_name: str
    scope_description: str | None
    scope_context: str | None
    space_name: str
    tables: list[_PromptTable]
    existing_relationships: list[_PromptRelationship]
    table_knowledge: list[_PromptTableKnowledge]
    pipelines: list[_PromptPipeline]
    previous_findings: _PromptPrevious | None


class ScopeSubgraphAgent(ProposalAgent[ScopeCatalogContext, ScopeProposal]):
    """Asks the model which table links and summary belong on the scope."""

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
        self._call: ModelCall[ScopeCatalogContext, ScopeProposal] = ModelCall(
            _scope_agent(model),
            name="scope-subgraph",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )

    def propose(self, context: ScopeCatalogContext) -> ScopeProposal:
        """Return the summary and links the model selected."""
        prompt: str = _prompt(context)
        selected: ScopeProposal = self._call.run(prompt, context)
        return selected


def _scope_agent(model: Model) -> Agent[ScopeCatalogContext, ScopeProposal]:
    """JSON schema. A tool call lets Qwen think until the answer is empty."""
    agent: Agent[ScopeCatalogContext, ScopeProposal] = Agent(
        model,
        deps_type=ScopeCatalogContext,
        output_type=structured_output(model, ScopeProposal),
        name="scope-subgraph",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    agent.output_validator(_accept_output)
    return agent


def _accept_output(ctx: RunContext[ScopeCatalogContext], proposed: ScopeProposal) -> ScopeProposal:
    try:
        accept_table_links(ctx.deps, proposed.links)
    except ValidationAppError as err:
        raise ModelRetry(str(err)) from err
    missing: list[str] = omitted_pipeline_names(proposed.summary, ctx.deps.pipelines)
    if len(missing) > 0:
        names: str = ", ".join(missing)
        raise ModelRetry(f"summary must name every attached pipeline and say what it does: {names}")
    return proposed


def _prompt(context: ScopeCatalogContext) -> str:
    missing: set[UUID] = set(context.tables_without_context)
    tables: list[_PromptTable] = []
    table: ScopeTableSummary
    for table in context.tables:
        columns: list[_PromptColumn] = []
        column: ColumnMetadata
        for column in table.columns:
            columns.append(
                _PromptColumn(
                    column_id=column.id,
                    name=column.name,
                    data_type=column.data_type,
                    is_primary_key=column.is_primary_key,
                    is_unique=column.is_unique,
                    is_foreign_key=column.is_foreign_key,
                    description=column.description,
                    context=column.context,
                )
            )
        tables.append(
            _PromptTable(
                table_id=table.id,
                name=table.name,
                description=table.description,
                context=table.context,
                row_count=table.row_count,
                has_table_context=table.id not in missing,
                columns=columns,
            )
        )
    relationships: list[_PromptRelationship] = []
    relation: TableRelation
    for relation in context.relationships:
        relationships.append(
            _PromptRelationship(
                relationship_type=relation.relationship_type.value,
                origin=relation.origin.value,
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
    knowledge: list[_PromptTableKnowledge] = []
    item: TableKnowledgeInput
    for item in context.table_knowledge:
        knowledge.append(
            _PromptTableKnowledge(
                table_id=item.table_id,
                source=item.source,
                column_relations=[
                    _PromptColumnRelation(
                        source_column_id=column_relation.source_column_id,
                        target_column_id=column_relation.target_column_id,
                        kind=column_relation.kind.value,
                        reason=column_relation.reason,
                    )
                    for column_relation in item.column_relations
                ],
            )
        )
    payload: _ScopeGraphPrompt = _ScopeGraphPrompt(
        scope_id=context.scope.id,
        scope_name=context.scope.name,
        scope_description=context.scope.description,
        scope_context=context.scope.context,
        space_name=context.space.name,
        tables=tables,
        existing_relationships=relationships,
        table_knowledge=knowledge,
        pipelines=[_pipeline(item) for item in context.pipelines],
        previous_findings=_previous(context.previous),
    )
    rendered: str = payload.model_dump_json(exclude_none=True)
    return rendered


def _pipeline(item: PipelineInput) -> _PromptPipeline:
    return _PromptPipeline(
        dag_id=item.dag_id,
        instance_name=item.instance_name,
        instance_context=item.instance_context,
        description=item.description,
        summary=item.summary,
        schedule=item.schedule,
        reads=list(item.reads),
        writes=list(item.writes),
        unknown=list(item.unknown),
    )


def _previous(previous: PreviousScopeFindings | None) -> _PromptPrevious | None:
    if previous is None:
        return None
    links: list[_PromptLink] = []
    link: TableLink
    for link in previous.links:
        links.append(
            _PromptLink(
                source_table_id=link.source_table_id,
                target_table_id=link.target_table_id,
                source_column_id=link.source_column_id,
                target_column_id=link.target_column_id,
                kind=link.kind.value,
                reason=link.reason,
            )
        )
    return _PromptPrevious(summary=previous.summary, links=links)
