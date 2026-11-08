"""Structured model call that writes business context for one schema agent."""

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.knowledge.models import (
    SchemaCatalogContext,
    SchemaContextProposal,
    SchemaTableFact,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import GraphCall
from intelegent_data_catalog.llm.spec import AgentSpec
from intelegent_data_catalog.llm.types import ThinkingLevel

_INSTRUCTIONS: str = (
    "You write business context for one schema. "
    "The user message is JSON listing the tables and columns in that schema. "
    "Return two to four sentences on what the schema holds and how the tables fit together. "
    "Do not invent tables or columns. "
    "Do not invent facts that the names and descriptions do not support."
)


class _PromptColumn(BaseModel):
    """One column sent with a schema table."""

    model_config = ConfigDict(extra="forbid")

    name: str
    data_type: str | None = None


class _PromptTable(BaseModel):
    """One table inside a schema context prompt."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    columns: list[_PromptColumn]
    neighbors: list[str]
    lineage: list[str]


class _SchemaPrompt(BaseModel):
    """User message for one schema."""

    model_config = ConfigDict(extra="forbid")

    catalog: str
    namespace: str
    existing_context: str | None
    tables: list[_PromptTable]


class SchemaContextAgent(ProposalAgent[SchemaCatalogContext, SchemaContextProposal]):
    """Asks the model for one schema's business context."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        thinking: ThinkingLevel = False,
    ) -> None:
        self._call: GraphCall[SchemaCatalogContext, SchemaContextProposal] = GraphCall(
            _schema_agent(model),
            name="schema-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )

    def propose(self, context: SchemaCatalogContext) -> SchemaContextProposal:
        """Return the context the model wrote for the schema."""
        return self._call.run(_prompt(context).model_dump_json(exclude_none=True), context)


def _schema_agent(model: BaseChatModel) -> AgentSpec[SchemaCatalogContext, SchemaContextProposal]:
    """JSON schema. A tool call lets Qwen think until the answer is empty."""
    return AgentSpec(
        model,
        output_type=SchemaContextProposal,
        instructions=_INSTRUCTIONS,
        retries=2,
        approve_output=True,
    )


def _prompt(context: SchemaCatalogContext) -> _SchemaPrompt:
    """Drop ids. The model sees names, types, neighbors, and lineage labels."""
    tables: list[_PromptTable] = []
    table: SchemaTableFact
    for table in context.tables:
        columns: list[_PromptColumn] = [
            _PromptColumn(name=column.name, data_type=column.data_type) for column in table.columns
        ]
        tables.append(
            _PromptTable(
                name=table.name,
                description=table.description,
                columns=columns,
                neighbors=list(table.neighbors),
                lineage=list(table.lineage),
            ),
        )
    return _SchemaPrompt(
        catalog=context.catalog,
        namespace=context.namespace,
        existing_context=context.existing_context,
        tables=tables,
    )
