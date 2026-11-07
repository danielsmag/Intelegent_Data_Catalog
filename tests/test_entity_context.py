"""Business context written through the table agent."""

import json
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryFeedbackPart,
    RetryPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.graph.document import GraphView
from intelegent_data_catalog.knowledge.context.catalog import PostgresCatalogContextProvider
from intelegent_data_catalog.knowledge.context.lineage import EmptyLineageReader
from intelegent_data_catalog.knowledge.factories.table_agent_factory import (
    TableAgentFactory,
    build_table_agent_factory,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnContextInput,
    ColumnContextItem,
    ColumnContextProposal,
    ColumnDataProfile,
    ColumnRelation,
    ColumnRelationKind,
    DataAccessMode,
    TableAnalysisContext,
    TableCatalogContext,
    TableColumnsContextInput,
    TableColumnsContextProposal,
    TableContextInput,
    TableContextProposal,
    TableDataProfile,
)
from intelegent_data_catalog.knowledge.profiling.profiler import UnconfiguredDataProfiler
from intelegent_data_catalog.knowledge.store import KnowledgeContextStore
from intelegent_data_catalog.llm.agent import TableModelAgent
from intelegent_data_catalog.llm.catalog import TableOperationAgent
from intelegent_data_catalog.llm.tools.table import TableToolSource
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import ContextOrigin, DataSourceType, TableKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import ColumnRow
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    CatalogTableCreate,
    ColumnCreate,
    DataSourceCreate,
    SourceRef,
)
from tests.registry_factory import OpenRegistry, open_registry
from tests.test_knowledge_builder import _context


def test_context_writers_disable_thinking() -> None:
    """Thinking models answer in the reasoning channel and leave content empty."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart(content="unused")])

    model: FunctionModel = FunctionModel(respond)
    table: TableOperationAgent = TableOperationAgent(
        model,
        max_requests=1,
        temperature=0.0,
        max_tokens=32,
        timeout_seconds=1.0,
    )
    assert table._table._settings["thinking"] is False
    assert table._columns._settings["thinking"] is False
    assert table._column._settings["thinking"] is False


def test_table_agent_retries_an_invented_column() -> None:
    """A column id outside the batch is sent back, then the real ids are accepted."""
    catalog: TableCatalogContext = _context(uuid4())
    column_id: UUID = catalog.columns[0].id
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        if _feedback(messages) == "":
            return _columns_response("invented", "Invented column.")
        assert "not in this batch" in _feedback(messages)
        return _columns_response("id", "Buyer who placed the order.")

    selected: TableColumnsContextProposal = _model_agent(respond).describe_columns(
        TableColumnsContextInput(table=catalog, columns=catalog.columns)
    )
    assert selected.columns[0].column_id == column_id
    assert selected.columns[0].context == "Buyer who placed the order."
    assert '"name":"id"' in prompts[0]
    assert str(column_id) not in prompts[0]


def test_table_agent_describes_a_column_with_saved_stats(tmp_path: Path) -> None:
    """Saved profile statistics reach the prompt, and the row stores LLM origin."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        table_id: UUID
        column_id: UUID
        table_id, column_id = _column(session)
        _save_profile(session, table_id, column_id)
        prompts: list[str] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del info
            prompts.append(_user_text(messages))
            return _text_response("Buyer who placed the order.")

        written: str = (
            _factory(session, _model_agent(respond)).create(table_id).describe_column(column_id)
        )
        assert written == "Buyer who placed the order."
        stored: ColumnRow | None = session.get(ColumnRow, column_id)
        assert stored is not None
        assert stored.context == "Buyer who placed the order."
        assert stored.context_origin == ContextOrigin.LLM.value
        assert "0.2" in prompts[0]
        assert "customer_id points at the customer key" in prompts[0]
        assert "alice" not in prompts[0]
    finally:
        session.close()
        database.dispose()


def test_table_agent_describes_columns_in_batches(tmp_path: Path) -> None:
    """More than one batch is used once a table has over 25 columns."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        table_id: UUID = _table(session)
        service: OpenRegistry = open_registry(session)
        index: int
        for index in range(26):
            service.tables.create_column(
                table_id, ColumnCreate(name=f"c{index:02d}", ordinal_position=index)
            )
        seen: list[int] = []

        class _Agent:
            def run(self, instruction: str, source: TableToolSource, instructions: str) -> str:
                del instruction, source, instructions
                raise AssertionError("no operation was requested")

            def describe_table(self, context: TableContextInput) -> TableContextProposal:
                del context
                raise AssertionError("table context was not requested")

            def describe_columns(
                self,
                context: TableColumnsContextInput,
            ) -> TableColumnsContextProposal:
                seen.append(len(context.columns))
                items: list[ColumnContextItem] = [
                    ColumnContextItem(column_id=column.id, context=f"Meaning of {column.name}.")
                    for column in context.columns
                ]
                return TableColumnsContextProposal(columns=items)

            def describe_column(self, context: ColumnContextInput) -> ColumnContextProposal:
                del context
                raise AssertionError("single column context was not requested")

        written: list[ColumnContextItem] = (
            _factory(session, _Agent()).create(table_id).describe_columns()
        )
        assert seen == [25, 1]
        assert len(written) == 26
        rows: list[ColumnRow] = RegistryRepository(session).list_columns(table_id)
        row: ColumnRow
        for row in rows:
            assert row.context_origin == ContextOrigin.LLM.value
            assert row.context is not None
            assert row.context.startswith("Meaning of ")
    finally:
        session.close()
        database.dispose()


class _NoGraph:
    def relationships(self, table_id: UUID, depth: int) -> GraphView:
        del table_id, depth
        return GraphView(nodes=[], edges=[], truncated=False)


def _factory(session: Session, agent: TableModelAgent) -> TableAgentFactory:
    return build_table_agent_factory(
        session,
        _NoGraph(),
        EmptyLineageReader(),
        UnconfiguredDataProfiler(),
        operation_agent=agent,
    )


def _model_agent(
    respond: Callable[[list[ModelMessage], AgentInfo], ModelResponse],
) -> TableOperationAgent:
    return TableOperationAgent(
        FunctionModel(respond),
        max_requests=4,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )


def _columns_response(name: str, context: str) -> ModelResponse:
    payload: str = json.dumps({"columns": [{"name": name, "context": context}]})
    return ModelResponse(parts=[TextPart(content=payload)])


def _text_response(context: str) -> ModelResponse:
    payload: str = json.dumps({"context": context})
    return ModelResponse(parts=[TextPart(content=payload)])


def _save_profile(session: Session, table_id: UUID, column_id: UUID) -> None:
    catalog: TableCatalogContext = PostgresCatalogContextProvider(
        RegistryRepository(session)
    ).get_table_context(table_id)
    KnowledgeContextStore(session).save(
        TableAnalysisContext(
            table=catalog.table,
            columns=catalog.columns,
            data_profile=TableDataProfile(
                table_id=table_id,
                access_mode=DataAccessMode.PROFILE_ONLY,
                columns=[
                    ColumnDataProfile(
                        column_id=column_id,
                        uniqueness_ratio=0.2,
                        sample_values=["alice"],
                        minimum="alice",
                    )
                ],
            ),
            existing_relationships=[],
            graph_neighbors=[],
            lineage=None,
            column_relations=[
                ColumnRelation(
                    source_column_id=column_id,
                    target_column_id=uuid4(),
                    kind=ColumnRelationKind.REFERENCES,
                    reason="customer_id points at the customer key",
                )
            ],
        )
    )


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _table(session: Session) -> UUID:
    service: OpenRegistry = open_registry(session)
    source_id: UUID = service.sources.create_data_source(
        DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST)
    ).id
    return service.tables.create_table(
        CatalogTableCreate(
            data_source_id=source_id,
            name="orders",
            table_type=TableKind.TABLE,
            physical_catalog="polaris",
            physical_namespace="sales",
            physical_table="orders",
            source_ref=SourceRef(catalog="polaris", namespace="sales", table="orders"),
        ),
    ).id


def _column(session: Session) -> tuple[UUID, UUID]:
    table_id: UUID = _table(session)
    column_id: UUID = (
        open_registry(session)
        .tables.create_column(table_id, ColumnCreate(name="customer_id", ordinal_position=0))
        .id
    )
    return table_id, column_id


def _user_text(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    message: ModelMessage
    for message in messages:
        part: object
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                chunks.append(str(part.content))
    return "\n".join(chunks)


def _feedback(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    seen_user: bool = False
    message: ModelMessage
    for message in messages:
        part: object
        for part in message.parts:
            if isinstance(part, RetryPromptPart | RetryFeedbackPart):
                chunks.append(str(part.content))
            elif isinstance(part, UserPromptPart):
                if seen_user:
                    chunks.append(str(part.content))
                seen_user = True
    return "\n".join(chunks)
