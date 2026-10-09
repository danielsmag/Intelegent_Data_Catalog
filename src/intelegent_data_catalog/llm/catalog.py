"""Short-lived Pydantic AI agent for one catalog table.

One ``TableOperationAgent`` runs a tool-using operation and writes business
context for the table, a batch of its columns, or one column.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext
from pydantic_ai.models import Model

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.access import accept_named_column_contexts
from intelegent_data_catalog.knowledge.models import (
    ColumnContextInput,
    ColumnContextItem,
    ColumnContextProposal,
    ColumnDataProfile,
    ColumnMetadata,
    ColumnRelation,
    LineageDataset,
    TableAnalysisContext,
    TableColumnsContextInput,
    TableColumnsContextProposal,
    TableContextInput,
    TableContextProposal,
    TableRelation,
    _context_text,
)
from intelegent_data_catalog.llm.agent import TableModelAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.tools.table import (
    TableToolSource,
    compare_columns,
    get_column_profile,
    get_data_profile,
    get_lineage,
    get_metadata,
    get_neighbors,
    publish_graph,
)

_TABLE_INSTRUCTIONS: str = (
    "You write business context for one catalog table. "
    "The user message is JSON with the table, its columns, existing context, "
    "statistics, catalog relationships, lineage names, and subgraph links "
    "when a previous analysis or Neo4j subgraph exists. "
    "subgraph links are column relations already stored for this table. "
    "Statistics are ratios and counts. Cell values are not included. "
    "Return two to four sentences on what the table contains and its grain. "
    "Mention keys and how it joins only when the input shows them. "
    "Do not invent facts that the names, types, keys, statistics, or context do not support."
)

_COLUMNS_INSTRUCTIONS: str = (
    "You write business context for every column in the user message. "
    "Return one item per column. Copy each column name exactly. "
    "Each context is one to three sentences on what the column means in the business. "
    "Mention grain, keys, units, and allowed values only when the input shows them. "
    "Do not invent column names. "
    "Do not invent facts that the names, types, keys, statistics, or context do not support."
)

_COLUMN_INSTRUCTIONS: str = (
    "You write business context for one catalog column. "
    "The user message is JSON with the column, its table, existing context, "
    "statistics, and subgraph links when a previous analysis or Neo4j subgraph exists. "
    "Statistics are ratios and counts. Cell values are not included. "
    "Return two to four sentences on what the column means in the business. "
    "Mention grain, keys, units, and allowed values only when the input shows them. "
    "Do not invent facts that the names, types, keys, statistics, or context do not support."
)

_TEXT_LIMIT: int = 200


class _PromptStat(BaseModel):
    """Column statistics already stored on a table analysis."""

    model_config = ConfigDict(extra="forbid")

    null_ratio: float | None = None
    uniqueness_ratio: float | None = None
    distinct_count: int | None = None


class _PromptRelation(BaseModel):
    """A stored column link that touches the column being described."""

    model_config = ConfigDict(extra="forbid")

    other_column: str
    kind: str
    direction: Literal["outgoing", "incoming"]
    reason: str


class _PromptColumn(BaseModel):
    """Column facts sent while writing table or column context."""

    model_config = ConfigDict(extra="forbid")

    column_id: UUID
    name: str
    data_type: str | None
    is_primary_key: bool | None
    is_unique: bool | None
    is_foreign_key: bool
    description: str | None
    context: str | None
    stats: _PromptStat | None = None


class _PromptLink(BaseModel):
    """One stored column link from the table knowledge subgraph."""

    model_config = ConfigDict(extra="forbid")

    source_column: str
    target_column: str
    kind: str
    reason: str


class _TablePrompt(BaseModel):
    """User message for one table."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    table_name: str
    description: str | None
    existing_context: str | None
    row_count: int | None
    columns: list[_PromptColumn]
    relationships: list[str]
    subgraph: list[_PromptLink]
    upstream_tables: list[str]
    downstream_tables: list[str]


class _PromptNamedColumn(BaseModel):
    """Column facts for a batch. Names only. The model must not echo ids."""

    model_config = ConfigDict(extra="forbid")

    name: str
    data_type: str | None
    is_primary_key: bool | None
    is_unique: bool | None
    is_foreign_key: bool
    description: str | None
    context: str | None
    stats: _PromptStat | None = None


class _NamedColumn(BaseModel):
    """One column's context, keyed by the name the model was shown."""

    model_config = ConfigDict(extra="ignore")

    name: str
    context: str

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("column name is empty")
        cleaned: str = value.strip()
        if cleaned == "":
            raise ValueError("column name is empty")
        return cleaned

    @field_validator("context", mode="before")
    @classmethod
    def _clean_context(cls, value: object) -> str:
        return _context_text(value)


class _NamedColumns(BaseModel):
    """Context for every column in one batch, keyed by column name."""

    model_config = ConfigDict(extra="ignore")

    columns: list[_NamedColumn] = Field(default_factory=list, max_length=40)


class _ColumnsPrompt(BaseModel):
    """User message for one batch of columns on a table."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    table_name: str
    table_description: str | None
    table_context: str | None
    columns: list[_PromptNamedColumn]
    subgraph: list[_PromptLink]


class _ColumnPrompt(BaseModel):
    """User message for one column."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    table_name: str
    table_description: str | None
    table_context: str | None
    column: _PromptColumn
    source_description: str | None
    relations: list[_PromptRelation]
    upstream_tables: list[str]
    downstream_tables: list[str]


class _ColumnsDeps:
    """Columns the model was shown, used to reject invented ids."""

    def __init__(self, columns: list[ColumnMetadata]) -> None:
        self.columns: list[ColumnMetadata] = columns


class TableOperationAgent(TableModelAgent):
    """The model behind one table.

    ``run`` carries out one operation with catalog tools and may publish Neo4j.
    ``describe_*`` return business context as structured output.
    """

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._operation: ModelCall[TableToolSource, str] = ModelCall(
            _operation_agent(model),
            name="table-catalog",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        self._table: ModelCall[TableContextInput, TableContextProposal] = ModelCall(
            _table_context_agent(model),
            name="table-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        self._columns: ModelCall[_ColumnsDeps, _NamedColumns] = ModelCall(
            _columns_context_agent(model),
            name="table-columns-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        self._column: ModelCall[ColumnContextInput, ColumnContextProposal] = ModelCall(
            _column_context_agent(model),
            name="column-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    def run(self, instruction: str, source: TableToolSource, instructions: str) -> str:
        """Run the model until the operation finishes or the request limit is reached."""
        return self._operation.run(instruction, source, instructions)

    def describe_table(self, context: TableContextInput) -> TableContextProposal:
        """Return the context the model wrote for the table."""
        return self._table.run(_table_prompt(context).model_dump_json(exclude_none=True), context)

    def describe_columns(self, context: TableColumnsContextInput) -> TableColumnsContextProposal:
        """Return context for every column in ``context.columns``."""
        prompt: str = _columns_prompt(context).model_dump_json(exclude_none=True)
        drafted: _NamedColumns = self._columns.run(prompt, _ColumnsDeps(context.columns))
        pairs: list[tuple[str, str]] = [(item.name, item.context) for item in drafted.columns]
        items: list[ColumnContextItem] = accept_named_column_contexts(context.columns, pairs)
        return TableColumnsContextProposal(columns=items)

    def describe_column(self, context: ColumnContextInput) -> ColumnContextProposal:
        """Return the context the model wrote for ``context.column``."""
        prompt: str = _column_prompt(context).model_dump_json(exclude_none=True)
        return self._column.run(prompt, context)


def _operation_agent(model: Model) -> Agent[TableToolSource, str]:
    """Build the tool-using agent and attach its catalog tools."""
    agent: Agent[TableToolSource, str] = Agent(
        model,
        deps_type=TableToolSource,
        output_type=str,
        name="table-catalog",
    )
    agent.tool(get_metadata)
    agent.tool(get_data_profile)
    agent.tool(get_column_profile)
    agent.tool(compare_columns)
    agent.tool(get_neighbors)
    agent.tool(get_lineage)
    agent.tool(publish_graph)
    return agent


def _table_context_agent(model: Model) -> Agent[TableContextInput, TableContextProposal]:
    agent: Agent[TableContextInput, TableContextProposal] = Agent(
        model,
        deps_type=TableContextInput,
        output_type=TableContextProposal,
        name="table-context",
        instructions=_TABLE_INSTRUCTIONS,
        retries=2,
    )
    return agent


def _columns_context_agent(model: Model) -> Agent[_ColumnsDeps, _NamedColumns]:
    """Ask for column names, not ids. Ollama grammar-constrains the JSON when it can."""
    output_type: NativeOutput[_NamedColumns] | type[_NamedColumns]
    if bool(model.profile.get("supports_json_schema_output", False)):
        output_type = NativeOutput(_NamedColumns)
    else:
        output_type = _NamedColumns
    agent: Agent[_ColumnsDeps, _NamedColumns] = Agent(
        model,
        deps_type=_ColumnsDeps,
        output_type=output_type,
        name="table-columns-context",
        instructions=_COLUMNS_INSTRUCTIONS,
        retries=2,
    )
    agent.output_validator(_accept_columns)
    return agent


def _column_context_agent(model: Model) -> Agent[ColumnContextInput, ColumnContextProposal]:
    agent: Agent[ColumnContextInput, ColumnContextProposal] = Agent(
        model,
        deps_type=ColumnContextInput,
        output_type=ColumnContextProposal,
        name="column-context",
        instructions=_COLUMN_INSTRUCTIONS,
        retries=2,
    )
    return agent


def _accept_columns(
    ctx: RunContext[_ColumnsDeps],
    proposed: _NamedColumns,
) -> _NamedColumns:
    pairs: list[tuple[str, str]] = [(item.name, item.context) for item in proposed.columns]
    try:
        accept_named_column_contexts(ctx.deps.columns, pairs)
    except ValidationAppError as err:
        raise ModelRetry(str(err)) from err
    return proposed


def _table_prompt(context: TableContextInput) -> _TablePrompt:
    links: list[str] = []
    relation: TableRelation
    for relation in context.table.relationships:
        links.append(f"{relation.relationship_type.value}:{relation.origin.value}")
    return _TablePrompt(
        table_id=context.table.table.id,
        table_name=context.table.table.name,
        description=_clip(
            context.table.table.description or context.table.table.source_description
        ),
        existing_context=_clip(context.table.table.context),
        row_count=context.table.table.row_count,
        columns=[_prompt_column(column, context.saved) for column in context.table.columns],
        relationships=links,
        subgraph=_links(context.subgraph, _names(context.table.columns)),
        upstream_tables=_lineage_names(context.saved, upstream=True),
        downstream_tables=_lineage_names(context.saved, upstream=False),
    )


def _columns_prompt(context: TableColumnsContextInput) -> _ColumnsPrompt:
    return _ColumnsPrompt(
        table_id=context.table.table.id,
        table_name=context.table.table.name,
        table_description=_clip(context.table.table.description),
        table_context=_clip(context.table.table.context),
        columns=[_prompt_named_column(column, context.saved) for column in context.columns],
        subgraph=_links(
            _touching(context.subgraph, {column.id for column in context.columns}),
            _names(context.table.columns),
        ),
    )


def _column_prompt(context: ColumnContextInput) -> _ColumnPrompt:
    return _ColumnPrompt(
        table_id=context.table.table.id,
        table_name=context.table.table.name,
        table_description=_clip(context.table.table.description),
        table_context=_clip(context.table.table.context),
        column=_prompt_column(context.column, context.saved),
        source_description=_clip(context.column.source_description),
        relations=_relations(context.subgraph, context.column.id, _names(context.table.columns)),
        upstream_tables=_lineage_names(context.saved, upstream=True),
        downstream_tables=_lineage_names(context.saved, upstream=False),
    )


def _prompt_column(column: ColumnMetadata, saved: TableAnalysisContext | None) -> _PromptColumn:
    return _PromptColumn(
        column_id=column.id,
        name=column.name,
        data_type=column.data_type,
        is_primary_key=column.is_primary_key,
        is_unique=column.is_unique,
        is_foreign_key=column.is_foreign_key,
        description=_clip(column.description or column.source_description),
        context=_clip(column.context),
        stats=_stat(saved, column.id),
    )


def _prompt_named_column(
    column: ColumnMetadata,
    saved: TableAnalysisContext | None,
) -> _PromptNamedColumn:
    return _PromptNamedColumn(
        name=column.name,
        data_type=column.data_type,
        is_primary_key=column.is_primary_key,
        is_unique=column.is_unique,
        is_foreign_key=column.is_foreign_key,
        description=_clip(column.description or column.source_description),
        context=_clip(column.context),
        stats=_stat(saved, column.id),
    )


def _stat(saved: TableAnalysisContext | None, column_id: UUID) -> _PromptStat | None:
    """Ratios and counts only. Samples, minimums, and maximums stay out of the prompt."""
    if saved is None or saved.data_profile is None:
        return None
    profile: ColumnDataProfile
    for profile in saved.data_profile.columns:
        if profile.column_id == column_id:
            return _PromptStat(
                null_ratio=profile.null_ratio,
                uniqueness_ratio=profile.uniqueness_ratio,
                distinct_count=profile.distinct_count,
            )
    return None


def _relations(
    subgraph: list[ColumnRelation],
    column_id: UUID,
    names: dict[UUID, str],
) -> list[_PromptRelation]:
    relations: list[_PromptRelation] = []
    relation: ColumnRelation
    for relation in subgraph:
        if relation.source_column_id == column_id:
            other: UUID = relation.target_column_id
            direction: Literal["outgoing", "incoming"] = "outgoing"
        elif relation.target_column_id == column_id:
            other = relation.source_column_id
            direction = "incoming"
        else:
            continue
        relations.append(
            _PromptRelation(
                other_column=names.get(other, str(other)),
                kind=relation.kind.value,
                direction=direction,
                reason=relation.reason,
            )
        )
    return relations


def _links(subgraph: list[ColumnRelation], names: dict[UUID, str]) -> list[_PromptLink]:
    links: list[_PromptLink] = []
    relation: ColumnRelation
    for relation in subgraph:
        links.append(
            _PromptLink(
                source_column=names.get(relation.source_column_id, str(relation.source_column_id)),
                target_column=names.get(relation.target_column_id, str(relation.target_column_id)),
                kind=relation.kind.value,
                reason=relation.reason,
            )
        )
    return links


def _touching(subgraph: list[ColumnRelation], column_ids: set[UUID]) -> list[ColumnRelation]:
    touching: list[ColumnRelation] = []
    relation: ColumnRelation
    for relation in subgraph:
        if relation.source_column_id in column_ids or relation.target_column_id in column_ids:
            touching.append(relation)
    return touching


def _names(columns: list[ColumnMetadata]) -> dict[UUID, str]:
    return {column.id: column.name for column in columns}


def _lineage_names(saved: TableAnalysisContext | None, *, upstream: bool) -> list[str]:
    if saved is None or saved.lineage is None:
        return []
    datasets: list[LineageDataset] = (
        saved.lineage.upstream_datasets if upstream else saved.lineage.downstream_datasets
    )
    return [dataset.name for dataset in datasets]


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text[:_TEXT_LIMIT]
