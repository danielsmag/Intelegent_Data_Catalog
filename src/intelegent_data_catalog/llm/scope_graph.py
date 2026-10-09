"""Structured model call that selects cross-table links and a scope summary."""

import asyncio
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResult
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from intelegent_data_catalog.exceptions import LlmError, LlmUnavailableError, ValidationAppError
from intelegent_data_catalog.knowledge.context.scope_graph import accept_table_links
from intelegent_data_catalog.knowledge.models import (
    ColumnMetadata,
    PreviousScopeFindings,
    ScopeCatalogContext,
    ScopeProposal,
    ScopeTableSummary,
    TableKnowledgeInput,
    TableLink,
    TableRelation,
)
from intelegent_data_catalog.llm.trace import AgentTrace

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
    "Do not repeat a column pair that is already an existing catalog relationship. "
    "Treat previous_findings as a draft: keep a justified link, drop the rest, or add one. "
    "summary is two to four sentences on what this scope describes and how the tables join. "
    "Mention a table without context only when that gap matters. "
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


class _PromptTable(BaseModel):
    """One scope table sent to the model."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    name: str
    description: str | None
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


class _ScopeGraphPrompt(BaseModel):
    """User message for one scope-subgraph call."""

    model_config = ConfigDict(extra="forbid")

    scope_id: UUID
    scope_name: str
    scope_description: str | None
    space_name: str
    tables: list[_PromptTable]
    existing_relationships: list[_PromptRelationship]
    table_knowledge: list[_PromptTableKnowledge]
    previous_findings: _PromptPrevious | None


class ScopeSubgraphAgent:
    """Asks the model which table links and summary belong on the scope."""

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._max_requests: int = max_requests
        self._settings: ModelSettings = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout_seconds,
            "thinking": False,
        }
        self._agent: Agent[ScopeCatalogContext, ScopeProposal] = _scope_agent(model)

    def propose(self, context: ScopeCatalogContext) -> ScopeProposal:
        """Return the summary and links the model selected."""
        return asyncio.run(self._propose(context))

    async def _propose(self, context: ScopeCatalogContext) -> ScopeProposal:
        prompt: str = _prompt(context)
        trace: AgentTrace = AgentTrace("scope-subgraph")
        trace.start(prompt)
        try:
            result: AgentRunResult[ScopeProposal] = await self._agent.run(
                prompt,
                deps=context,
                model_settings=self._settings,
                usage_limits=UsageLimits(request_limit=self._max_requests),
            )
        except UsageLimitExceeded as err:
            raise LlmError("language model exceeded the tool step limit") from err
        except ModelHTTPError as err:
            raise LlmError(f"language model returned {err.status_code}") from err
        except ModelAPIError as err:
            raise LlmError("language model is unavailable") from err
        except UnexpectedModelBehavior as err:
            raise LlmError("language model returned an unexpected response") from err
        except ToolRetryError as err:
            raise LlmError("language model returned an unexpected response") from err
        except UserError as err:
            raise LlmUnavailableError("language model is not configured") from err
        trace.finish(result)
        selected: ScopeProposal = result.output
        return selected


def _scope_agent(model: Model) -> Agent[ScopeCatalogContext, ScopeProposal]:
    agent: Agent[ScopeCatalogContext, ScopeProposal] = Agent(
        model,
        deps_type=ScopeCatalogContext,
        output_type=ScopeProposal,
        name="scope-subgraph",
        instructions=_INSTRUCTIONS,
    )
    agent.output_validator(_accept_output)
    return agent


def _accept_output(ctx: RunContext[ScopeCatalogContext], proposed: ScopeProposal) -> ScopeProposal:
    try:
        accept_table_links(ctx.deps, proposed.links)
    except ValidationAppError as err:
        raise ModelRetry(str(err)) from err
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
                )
            )
        tables.append(
            _PromptTable(
                table_id=table.id,
                name=table.name,
                description=table.description,
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
        space_name=context.space.name,
        tables=tables,
        existing_relationships=relationships,
        table_knowledge=knowledge,
        previous_findings=_previous(context.previous),
    )
    rendered: str = payload.model_dump_json(exclude_none=True)
    return rendered


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
