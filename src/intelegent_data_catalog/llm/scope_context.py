"""Structured model call that writes business context for one scope."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models import Model

from intelegent_data_catalog.knowledge.models import (
    ScopeCatalogContext,
    ScopeContextProposal,
    ScopeTableSummary,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

_INSTRUCTIONS: str = (
    "You write business context for one business scope. "
    "The user message is JSON with the scope, its tables and columns, existing context, "
    "and the last scope summary when one exists. "
    "Return two to four sentences on what this scope describes and how its tables fit together. "
    "Do not invent tables. "
    "Do not invent facts that the names, descriptions, or context do not support."
)

_TEXT_LIMIT: int = 200


class _PromptColumn(BaseModel):
    """Column inside a scope context prompt."""

    model_config = ConfigDict(extra="forbid")

    name: str
    data_type: str | None
    context: str | None


class _PromptTable(BaseModel):
    """One table inside a scope context prompt."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None
    context: str | None
    columns: list[_PromptColumn]


class _ScopePrompt(BaseModel):
    """User message for one scope."""

    model_config = ConfigDict(extra="forbid")

    scope_id: UUID
    scope_name: str
    description: str | None
    existing_context: str | None
    summary: str | None
    tables: list[_PromptTable]


class ScopeContextAgent(ProposalAgent[ScopeCatalogContext, ScopeContextProposal]):
    """Asks the model for one scope's business context."""

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._call: ModelCall[ScopeCatalogContext, ScopeContextProposal] = ModelCall(
            _scope_agent(model),
            name="scope-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=False,
        )

    def propose(self, context: ScopeCatalogContext) -> ScopeContextProposal:
        """Return the context the model wrote for the scope."""
        return self._call.run(_prompt(context).model_dump_json(exclude_none=True), context)


def _scope_agent(model: Model) -> Agent[ScopeCatalogContext, ScopeContextProposal]:
    """JSON schema. A tool call lets Qwen think until the answer is empty."""
    agent: Agent[ScopeCatalogContext, ScopeContextProposal] = Agent(
        model,
        deps_type=ScopeCatalogContext,
        output_type=structured_output(model, ScopeContextProposal),
        name="scope-context",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    return agent


def _prompt(context: ScopeCatalogContext) -> _ScopePrompt:
    tables: list[_PromptTable] = []
    table: ScopeTableSummary
    for table in context.tables:
        tables.append(
            _PromptTable(
                name=table.name,
                description=_clip(table.description),
                context=_clip(table.context),
                columns=[
                    _PromptColumn(
                        name=column.name,
                        data_type=column.data_type,
                        context=_clip(column.context),
                    )
                    for column in table.columns
                ],
            )
        )
    summary: str | None = None if context.previous is None else _clip(context.previous.summary)
    return _ScopePrompt(
        scope_id=context.scope.id,
        scope_name=context.scope.name,
        description=_clip(context.scope.description),
        existing_context=_clip(context.scope.context),
        summary=summary,
        tables=tables,
    )


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text[:_TEXT_LIMIT]
