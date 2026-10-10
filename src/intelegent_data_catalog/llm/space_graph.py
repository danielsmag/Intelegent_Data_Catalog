"""Structured model call that selects cross-scope links and a space summary."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.context.space_graph import accept_space_links
from intelegent_data_catalog.knowledge.models import (
    ColumnMetadata,
    PreviousSpaceFindings,
    ScopeGraphEdge,
    ScopeKnowledgeInput,
    ScopeTableSummary,
    SpaceCatalogContext,
    SpaceProposal,
    SpaceScopeSummary,
    TableLink,
    TableRelation,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

_INSTRUCTIONS: str = (
    "You build a space overview for one business space. "
    "The user message is JSON with the space, its scopes, the tables and columns in each scope, "
    "existing catalog relationships that already cross scopes, "
    "scope_knowledge from each scope agent, "
    "and previous_findings from the last space build when one exists. "
    "scope_knowledge.links are column pairs the scope agent stored inside one scope. "
    "scope_knowledge.graph shows how that scope agent connected its own tables. "
    "Use both to find shared keys that cross scopes. "
    "Return a short summary and the important links between tables in different scopes. "
    "Each link uses one column pair and must join two scopes. "
    "REFERENCES means the source column points at the target column, usually a key. "
    "JOINS_ON means the columns are a join key and not a declared foreign key. "
    "Prefer keys, foreign keys, and shared identifiers. "
    "Ignore weak name matches and links that stay inside one scope. "
    "context fields hold the business meaning written for the space, scopes, tables, and columns. "
    "Use them to judge which links matter. "
    "Do not repeat a column pair that is already an existing catalog relationship. "
    "Treat previous_findings as a draft: keep a justified link, drop the rest, or add one. "
    "summary is two to four sentences on what this space describes and how the scopes join. "
    "Mention a scope without context only when that gap matters. "
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
    """One table sent to the model."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    name: str
    description: str | None
    context: str | None
    row_count: int | None
    columns: list[_PromptColumn]


class _PromptScope(BaseModel):
    """One scope sent to the model."""

    model_config = ConfigDict(extra="forbid")

    scope_id: UUID
    name: str
    description: str | None
    context: str | None
    has_scope_context: bool
    tables: list[_PromptTable]


class _PromptMapping(BaseModel):
    """Column pair already stored on a catalog relationship."""

    model_config = ConfigDict(extra="forbid")

    source_column_id: UUID
    target_column_id: UUID


class _PromptRelationship(BaseModel):
    """Catalog relationship reduced to the facts that matter for space links."""

    model_config = ConfigDict(extra="forbid")

    relationship_type: str
    origin: str
    source_table_id: UUID
    target_table_id: UUID
    description: str | None
    column_mappings: list[_PromptMapping]


class _PromptStoredLink(BaseModel):
    """Column link already stored by a scope agent."""

    model_config = ConfigDict(extra="forbid")

    source_table_id: UUID
    target_table_id: UUID
    source_column_id: UUID
    target_column_id: UUID
    kind: str
    reason: str


class _PromptGraphEdge(BaseModel):
    """Table edge drawn on a scope knowledge subgraph."""

    model_config = ConfigDict(extra="forbid")

    source_table_id: UUID
    target_table_id: UUID
    kind: str
    reason: str


class _PromptScopeKnowledge(BaseModel):
    """Merged scope-agent findings for one scope."""

    model_config = ConfigDict(extra="forbid")

    scope_id: UUID
    source: str
    summary: str | None
    links: list[_PromptStoredLink]
    graph: list[_PromptGraphEdge]


class _PromptPrevious(BaseModel):
    """Draft the model may keep, drop, or extend."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None
    links: list[_PromptStoredLink]


class _SpaceGraphPrompt(BaseModel):
    """User message for one space-subgraph call."""

    model_config = ConfigDict(extra="forbid")

    space_id: UUID
    space_name: str
    space_description: str | None
    space_context: str | None
    scopes: list[_PromptScope]
    existing_relationships: list[_PromptRelationship]
    scope_knowledge: list[_PromptScopeKnowledge]
    previous_findings: _PromptPrevious | None


class SpaceSubgraphAgent(ProposalAgent[SpaceCatalogContext, SpaceProposal]):
    """Asks the model which cross-scope links and summary belong on the space."""

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._call: ModelCall[SpaceCatalogContext, SpaceProposal] = ModelCall(
            _space_agent(model),
            name="space-subgraph",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=False,
        )

    def propose(self, context: SpaceCatalogContext) -> SpaceProposal:
        """Return the summary and links the model selected."""
        prompt: str = _prompt(context)
        selected: SpaceProposal = self._call.run(prompt, context)
        return selected


def _space_agent(model: Model) -> Agent[SpaceCatalogContext, SpaceProposal]:
    """JSON schema. A tool call lets Qwen think until the answer is empty."""
    agent: Agent[SpaceCatalogContext, SpaceProposal] = Agent(
        model,
        deps_type=SpaceCatalogContext,
        output_type=structured_output(model, SpaceProposal),
        name="space-subgraph",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    agent.output_validator(_accept_output)
    return agent


def _accept_output(ctx: RunContext[SpaceCatalogContext], proposed: SpaceProposal) -> SpaceProposal:
    try:
        accept_space_links(ctx.deps, proposed.links)
    except ValidationAppError as err:
        raise ModelRetry(str(err)) from err
    return proposed


def _prompt(context: SpaceCatalogContext) -> str:
    missing: set[UUID] = set(context.scopes_without_context)
    scopes: list[_PromptScope] = []
    scope: SpaceScopeSummary
    for scope in context.scopes:
        tables: list[_PromptTable] = []
        table: ScopeTableSummary
        for table in scope.tables:
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
                    columns=columns,
                )
            )
        scopes.append(
            _PromptScope(
                scope_id=scope.id,
                name=scope.name,
                description=scope.description,
                context=scope.context,
                has_scope_context=scope.id not in missing,
                tables=tables,
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
    knowledge: list[_PromptScopeKnowledge] = []
    item: ScopeKnowledgeInput
    for item in context.scope_knowledge:
        knowledge.append(
            _PromptScopeKnowledge(
                scope_id=item.scope_id,
                source=item.source,
                summary=item.summary,
                links=[_stored_link(link) for link in item.links],
                graph=[_graph_edge(edge) for edge in item.graph],
            )
        )
    payload: _SpaceGraphPrompt = _SpaceGraphPrompt(
        space_id=context.space.id,
        space_name=context.space.name,
        space_description=context.space.description,
        space_context=context.space.context,
        scopes=scopes,
        existing_relationships=relationships,
        scope_knowledge=knowledge,
        previous_findings=_previous(context.previous),
    )
    rendered: str = payload.model_dump_json(exclude_none=True)
    return rendered


def _stored_link(link: TableLink) -> _PromptStoredLink:
    return _PromptStoredLink(
        source_table_id=link.source_table_id,
        target_table_id=link.target_table_id,
        source_column_id=link.source_column_id,
        target_column_id=link.target_column_id,
        kind=link.kind.value,
        reason=link.reason,
    )


def _graph_edge(edge: ScopeGraphEdge) -> _PromptGraphEdge:
    return _PromptGraphEdge(
        source_table_id=edge.source_table_id,
        target_table_id=edge.target_table_id,
        kind=edge.kind.value,
        reason=edge.reason,
    )


def _previous(previous: PreviousSpaceFindings | None) -> _PromptPrevious | None:
    if previous is None:
        return None
    return _PromptPrevious(
        summary=previous.summary,
        links=[_stored_link(link) for link in previous.links],
    )
