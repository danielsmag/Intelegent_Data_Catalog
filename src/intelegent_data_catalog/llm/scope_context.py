"""Structured model call that writes business context for one scope."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.settings import ThinkingLevel

from intelegent_data_catalog.knowledge.models import (
    PipelineInput,
    ScopeCatalogContext,
    ScopeContextProposal,
    ScopeTableSummary,
    omitted_pipeline_names,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

_INSTRUCTIONS: str = (
    "You write business context for one business scope. "
    "The user message is JSON with the scope, its tables and columns, existing context, "
    "and the last scope summary when one exists. "
    "Return two to four sentences on what this scope describes and how its tables fit together. "
    "pipelines lists Airflow DAGs attached to this scope. "
    "instance_context is the business meaning already written for that Airflow instance. "
    "description is the DAG text. "
    "reads, writes, and unknown are table names. "
    "unknown means the DAG mentions the table and the direction is not known. "
    "Tables written by one pipeline from the same inputs usually share keys. "
    "Use pipelines and instance_context to describe the business process. "
    "When pipelines is not empty, name every dag_id and say what that pipeline does. "
    "Use the description and those table names in that sentence. "
    "Do not invent tables. "
    "Do not invent facts that the names, descriptions, or context do not support."
)


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


class _PromptPipeline(BaseModel):
    """One attached DAG sent with a scope."""

    model_config = ConfigDict(extra="forbid")

    dag_id: str
    instance_name: str
    instance_context: str | None
    description: str | None
    summary: str | None
    schedule: str | None
    reads: list[str]
    writes: list[str]
    unknown: list[str]


class _ScopePrompt(BaseModel):
    """User message for one scope."""

    model_config = ConfigDict(extra="forbid")

    scope_id: UUID
    scope_name: str
    description: str | None
    existing_context: str | None
    summary: str | None
    tables: list[_PromptTable]
    pipelines: list[_PromptPipeline]


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
        text_chars: int = 200,
        thinking: ThinkingLevel = False,
    ) -> None:
        self._text_chars: int = text_chars
        self._call: ModelCall[ScopeCatalogContext, ScopeContextProposal] = ModelCall(
            _scope_agent(model),
            name="scope-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )

    def propose(self, context: ScopeCatalogContext) -> ScopeContextProposal:
        """Return the context the model wrote for the scope."""
        prompt: _ScopePrompt = _prompt(context, self._text_chars)
        return self._call.run(prompt.model_dump_json(exclude_none=True), context)


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
    agent.output_validator(_accept_output)
    return agent


def _accept_output(
    ctx: RunContext[ScopeCatalogContext],
    proposed: ScopeContextProposal,
) -> ScopeContextProposal:
    missing: list[str] = omitted_pipeline_names(proposed.context, ctx.deps.pipelines)
    if len(missing) > 0:
        names: str = ", ".join(missing)
        raise ModelRetry(f"context must name every attached pipeline and say what it does: {names}")
    return proposed


def _prompt(context: ScopeCatalogContext, text_chars: int) -> _ScopePrompt:
    tables: list[_PromptTable] = []
    names: dict[UUID, str] = {}
    table: ScopeTableSummary
    for table in context.tables:
        names[table.id] = table.name
        tables.append(
            _PromptTable(
                name=table.name,
                description=_clip(table.description, text_chars),
                context=_clip(table.context, text_chars),
                columns=[
                    _PromptColumn(
                        name=column.name,
                        data_type=column.data_type,
                        context=_clip(column.context, text_chars),
                    )
                    for column in table.columns
                ],
            )
        )
    summary: str | None = (
        None if context.previous is None else _clip(context.previous.summary, text_chars)
    )
    return _ScopePrompt(
        scope_id=context.scope.id,
        scope_name=context.scope.name,
        description=_clip(context.scope.description, text_chars),
        existing_context=_clip(context.scope.context, text_chars),
        summary=summary,
        tables=tables,
        pipelines=[_pipeline(item, names, text_chars) for item in context.pipelines],
    )


def _pipeline(item: PipelineInput, names: dict[UUID, str], text_chars: int) -> _PromptPipeline:
    return _PromptPipeline(
        dag_id=item.dag_id,
        instance_name=item.instance_name,
        instance_context=item.instance_context,
        description=_clip(item.description, text_chars),
        summary=item.summary,
        schedule=item.schedule,
        reads=_table_names(item.reads, names),
        writes=_table_names(item.writes, names),
        unknown=_table_names(item.unknown, names),
    )


def _table_names(table_ids: list[UUID], names: dict[UUID, str]) -> list[str]:
    found: list[str] = []
    table_id: UUID
    for table_id in table_ids:
        name: str | None = names.get(table_id)
        if name is None:
            continue
        found.append(name)
    return found


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text[:limit]
