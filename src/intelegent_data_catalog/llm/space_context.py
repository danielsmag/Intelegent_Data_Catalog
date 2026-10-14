"""Structured model call that writes business context for one space."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from intelegent_data_catalog.knowledge.models import (
    PipelineInput,
    ScopeTableSummary,
    SpaceCatalogContext,
    SpaceContextProposal,
    SpaceScopeSummary,
    omitted_pipeline_names,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

_INSTRUCTIONS: str = (
    "You write business context for one business space. "
    "The user message is JSON with the space, its scopes, and the tables in each scope. "
    "A previous space summary is included when one exists. "
    "Return two to four sentences on what this space describes and how its scopes fit together. "
    "pipelines lists Airflow DAGs attached to this space. "
    "instance_context is the business meaning already written for that Airflow instance. "
    "Tables written by one pipeline from the same inputs usually share keys. "
    "Use pipelines and instance_context to describe the business process. "
    "When pipelines is not empty, name every dag_id and say what that pipeline does. "
    "Do that even when the space has one scope and no cross-scope link. "
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


class _PromptPipeline(BaseModel):
    """One attached DAG sent with a space."""

    model_config = ConfigDict(extra="forbid")

    dag_id: str
    instance_name: str
    instance_context: str | None
    summary: str | None
    schedule: str | None


class _SpacePrompt(BaseModel):
    """User message for one space."""

    model_config = ConfigDict(extra="forbid")

    space_id: UUID
    space_name: str
    description: str | None
    existing_context: str | None
    summary: str | None
    scopes: list[_PromptScope]
    pipelines: list[_PromptPipeline]


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
            thinking=False,
        )

    def propose(self, context: SpaceCatalogContext) -> SpaceContextProposal:
        """Return the context the model wrote for the space."""
        return self._call.run(_prompt(context).model_dump_json(exclude_none=True), context)


def _space_agent(model: Model) -> Agent[SpaceCatalogContext, SpaceContextProposal]:
    """JSON schema. A tool call lets Qwen think until the answer is empty."""
    agent: Agent[SpaceCatalogContext, SpaceContextProposal] = Agent(
        model,
        deps_type=SpaceCatalogContext,
        output_type=structured_output(model, SpaceContextProposal),
        name="space-context",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    agent.output_validator(_accept_output)
    return agent


def _accept_output(
    ctx: RunContext[SpaceCatalogContext],
    proposed: SpaceContextProposal,
) -> SpaceContextProposal:
    missing: list[str] = omitted_pipeline_names(proposed.context, ctx.deps.pipelines)
    if len(missing) > 0:
        names: str = ", ".join(missing)
        raise ModelRetry(f"context must name every attached pipeline and say what it does: {names}")
    return proposed


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
        pipelines=[_pipeline(item) for item in context.pipelines],
    )


def _pipeline(item: PipelineInput) -> _PromptPipeline:
    return _PromptPipeline(
        dag_id=item.dag_id,
        instance_name=item.instance_name,
        instance_context=item.instance_context,
        summary=item.summary,
        schedule=item.schedule,
    )


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text[:_TEXT_LIMIT]
