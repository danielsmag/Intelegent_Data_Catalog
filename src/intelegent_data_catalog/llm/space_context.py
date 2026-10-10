"""Structured model call that writes business context for one space."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models import Model

from intelegent_data_catalog.knowledge.models import (
    ScopeTableSummary,
    SpaceCatalogContext,
    SpaceContextProposal,
    SpaceScopeSummary,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall

_INSTRUCTIONS: str = (
    "You write business context for one business space. "
    "The user message is JSON with the space, its scopes, and the tables in each scope. "
    "A previous space summary is included when one exists. "
    "Return two to four sentences on what this space describes and how its scopes fit together. "
    "Do not invent scopes or tables. "
    "Do not invent facts that the names, descriptions, or context do not support."
)

_TEXT_LIMIT: int = 200


class _PromptTable(BaseModel):
    """One table name inside a space context prompt."""

    model_config = ConfigDict(extra="forbid")

    name: str
    context: str | None


class _PromptScope(BaseModel):
    """One scope inside a space context prompt."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None
    context: str | None
    tables: list[_PromptTable]


class _SpacePrompt(BaseModel):
    """User message for one space."""

    model_config = ConfigDict(extra="forbid")

    space_id: UUID
    space_name: str
    description: str | None
    existing_context: str | None
    summary: str | None
    scopes: list[_PromptScope]


class SpaceContextAgent(ProposalAgent[SpaceCatalogContext, SpaceContextProposal]):
    """Asks the model for one space's business context."""

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._call: ModelCall[SpaceCatalogContext, SpaceContextProposal] = ModelCall(
            _space_agent(model),
            name="space-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    def propose(self, context: SpaceCatalogContext) -> SpaceContextProposal:
        """Return the context the model wrote for the space."""
        return self._call.run(_prompt(context).model_dump_json(exclude_none=True), context)


def _space_agent(model: Model) -> Agent[SpaceCatalogContext, SpaceContextProposal]:
    agent: Agent[SpaceCatalogContext, SpaceContextProposal] = Agent(
        model,
        deps_type=SpaceCatalogContext,
        output_type=SpaceContextProposal,
        name="space-context",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    return agent


def _prompt(context: SpaceCatalogContext) -> _SpacePrompt:
    scopes: list[_PromptScope] = []
    scope: SpaceScopeSummary
    for scope in context.scopes:
        tables: list[_PromptTable] = []
        table: ScopeTableSummary
        for table in scope.tables:
            tables.append(_PromptTable(name=table.name, context=_clip(table.context)))
        scopes.append(
            _PromptScope(
                name=scope.name,
                description=_clip(scope.description),
                context=_clip(scope.context),
                tables=tables,
            )
        )
    summary: str | None = None if context.previous is None else _clip(context.previous.summary)
    return _SpacePrompt(
        space_id=context.space.id,
        space_name=context.space.name,
        description=_clip(context.space.description),
        existing_context=_clip(context.space.context),
        summary=summary,
        scopes=scopes,
    )


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text[:_TEXT_LIMIT]
