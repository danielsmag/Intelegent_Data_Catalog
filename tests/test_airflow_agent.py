"""Airflow instance agent: one model call per instance, LLM links, stored findings."""

import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from intelegent_data_catalog.airflow.repository import AirflowRepository, DagWrite, LlmMappingWrite
from intelegent_data_catalog.knowledge.context.airflow_catalog import (
    PostgresAirflowCatalogContextProvider,
)
from intelegent_data_catalog.knowledge.factories.airflow_agent_factory import (
    build_airflow_agent_factory,
)
from intelegent_data_catalog.knowledge.models import (
    AirflowAnalysisContext,
    AirflowCatalogContext,
    AirflowDagInput,
    AirflowProposal,
    DagCandidateTable,
    DagFinding,
)
from intelegent_data_catalog.knowledge.store import (
    AirflowKnowledgeContextStore,
    SemanticContextStore,
)
from intelegent_data_catalog.llm.airflow_analysis import AirflowAnalysisAgent
from intelegent_data_catalog.llm.airflow_context import AirflowContextAgent
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    AirflowInstanceStatus,
    AirflowMappingDirection,
    AirflowMappingOrigin,
    ContextOrigin,
    DataSourceType,
    TableKind,
)
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import AirflowDagTableRow, AirflowInstanceRow
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import CatalogTableCreate, DataSourceCreate, SourceRef
from tests.llm_fakes import (
    AgentInfo,
    FunctionModel,
    ModelMessage,
    ModelResponse,
    RetryFeedbackPart,
    RetryPromptPart,
    TextPart,
    UserPromptPart,
)
from tests.registry_factory import OpenRegistry, open_registry


def test_airflow_agent_retries_an_unknown_table() -> None:
    """A table id outside the candidate list is rejected, then a real one is kept."""
    dag_row_id: UUID = uuid4()
    table_id: UUID = uuid4()
    context: AirflowCatalogContext = AirflowCatalogContext(
        instance_id=uuid4(),
        instance_name="Local",
        dags=[
            AirflowDagInput(
                dag_row_id=dag_row_id,
                dag_id="append_sales",
                description="nightly append",
            )
        ],
        candidate_tables=[
            DagCandidateTable(
                id=table_id,
                qualified_name="polaris.sales.orders",
            )
        ],
    )

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        if _feedback(messages) == "":
            body: str = json.dumps(
                {
                    "summary": "Not yet.",
                    "dags": [
                        {
                            "dag_row_id": str(dag_row_id),
                            "dag_id": "append_sales",
                            "summary": "writes something",
                            "links": [
                                {
                                    "dag_row_id": str(dag_row_id),
                                    "table_id": "00000000-0000-0000-0000-000000000001",
                                    "direction": "OUTPUT",
                                    "reason": "invented table",
                                }
                            ],
                        }
                    ],
                }
            )
            return ModelResponse(parts=[TextPart(content=body)])
        accepted: str = json.dumps(
            {
                "summary": "Local runs the nightly append.",
                "dags": [
                    {
                        "dag_row_id": str(dag_row_id),
                        "dag_id": "append_sales",
                        "summary": "Appends orders.",
                        "links": [
                            {
                                "dag_row_id": str(dag_row_id),
                                "table_id": str(table_id),
                                "direction": "OUTPUT",
                                "reason": "source names orders",
                            }
                        ],
                    }
                ],
            }
        )
        return ModelResponse(parts=[TextPart(content=accepted)])

    agent: AirflowAnalysisAgent = AirflowAnalysisAgent(
        FunctionModel(respond),
        max_requests=4,
        temperature=0.0,
        max_tokens=512,
        timeout_seconds=5.0,
    )
    selected: AirflowProposal = agent.propose(context)
    assert selected.summary == "Local runs the nightly append."
    assert len(selected.dags) == 1
    assert selected.dags[0].links[0].table_id == table_id
    assert selected.dags[0].links[0].direction == AirflowMappingDirection.OUTPUT


def test_airflow_analysis_prompt_includes_instance_context() -> None:
    """The analysis prompt carries the business context stored on the instance."""
    context: AirflowCatalogContext = AirflowCatalogContext(
        instance_id=uuid4(),
        instance_name="Local",
        instance_context="Nightly sales loads.",
        dags=[],
        candidate_tables=[],
    )
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        prompts.append(_user_text(messages))
        return ModelResponse(parts=[TextPart(content='{"summary":"Loads.","dags":[]}')])

    agent: AirflowAnalysisAgent = AirflowAnalysisAgent(
        FunctionModel(respond),
        max_requests=2,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    agent.propose(context)
    assert "instance_context" in prompts[0]
    assert "Nightly sales loads." in prompts[0]


def test_replace_llm_mappings_keeps_manual_and_other_dags(tmp_path: Path) -> None:
    """Replacing LLM links leaves MANUAL, ASSET, and LLM rows on DAGs outside the call."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        table_id: UUID = _table(session)
        repo: AirflowRepository = AirflowRepository(session)
        instance_id: UUID = _instance(repo, "Local", "http://airflow.test")
        first: UUID = _dag(repo, instance_id, "append_sales")
        second: UUID = _dag(repo, instance_id, "other_job")
        repo.add_mapping(
            dag_row_id=first,
            table_id=table_id,
            origin=AirflowMappingOrigin.MANUAL.value,
            direction=AirflowMappingDirection.INPUT.value,
            evidence=None,
        )
        repo.add_mapping(
            dag_row_id=first,
            table_id=table_id,
            origin=AirflowMappingOrigin.LLM.value,
            direction=AirflowMappingDirection.OUTPUT.value,
            evidence="old",
        )
        repo.add_mapping(
            dag_row_id=second,
            table_id=table_id,
            origin=AirflowMappingOrigin.LLM.value,
            direction=AirflowMappingDirection.INPUT.value,
            evidence="keep",
        )
        repo.replace_llm_mappings(
            {first},
            [
                LlmMappingWrite(
                    dag_row_id=first,
                    table_id=table_id,
                    direction=AirflowMappingDirection.OUTPUT.value,
                    evidence="source names orders",
                )
            ],
        )
        repo.replace_discovered_mappings(instance_id, [])
        repo.commit()
        rows: list[AirflowDagTableRow] = repo.list_mappings_for_dags({first, second})
        keys: set[tuple[UUID, str]] = {(row.dag_row_id, row.origin) for row in rows}
        assert (first, AirflowMappingOrigin.MANUAL.value) in keys
        assert (first, AirflowMappingOrigin.LLM.value) in keys
        assert (second, AirflowMappingOrigin.LLM.value) in keys
        fresh: AirflowDagTableRow | None = repo.find_mapping(
            first,
            table_id,
            AirflowMappingOrigin.LLM.value,
        )
        assert fresh is not None
        assert fresh.evidence == "source names orders"
    finally:
        session.close()
        database.dispose()


def test_merge_findings_keeps_dags_that_were_not_analyzed(tmp_path: Path) -> None:
    """A partial build replaces only the DAGs it analyzed."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        repo: AirflowRepository = AirflowRepository(session)
        instance_id: UUID = _instance(repo, "Local", "http://airflow.test")
        repo.commit()
        kept: UUID = uuid4()
        replaced: UUID = uuid4()
        store: AirflowKnowledgeContextStore = AirflowKnowledgeContextStore(session)
        store.save(
            AirflowAnalysisContext(
                instance_id=instance_id,
                instance_name="Local",
                summary="All jobs.",
                findings=[
                    DagFinding(dag_row_id=kept, dag_id="kept", summary="Stays."),
                    DagFinding(dag_row_id=replaced, dag_id="replaced", summary="Old."),
                ],
                dags_analyzed=2,
            )
        )
        merged: AirflowAnalysisContext = store.merge_findings(
            AirflowAnalysisContext(
                instance_id=instance_id,
                instance_name="Local",
                summary="Only one job.",
                findings=[DagFinding(dag_row_id=replaced, dag_id="replaced", summary="New.")],
                dags_analyzed=1,
            )
        )
        by_dag: dict[str, str] = {item.dag_id: item.summary for item in merged.findings}
        assert by_dag["kept"] == "Stays."
        assert by_dag["replaced"] == "New."
        assert merged.summary == "All jobs."
    finally:
        session.close()
        database.dispose()


def test_describe_writes_instance_context(tmp_path: Path) -> None:
    """describe stores LLM business context and sends DAG source, tasks, and tables."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        table_id: UUID = _table(session)
        repo: AirflowRepository = AirflowRepository(session)
        instance_id: UUID = _instance(repo, "Local", "http://airflow.test")
        dag_row_id: UUID = _dag(
            repo,
            instance_id,
            "append_sales",
            source_code="writer.append_order()",
            tasks=[{"task_id": "write_order", "operator_name": "PythonOperator"}],
        )
        repo.add_mapping(
            dag_row_id=dag_row_id,
            table_id=table_id,
            origin=AirflowMappingOrigin.SOURCE_MATCH.value,
            direction=AirflowMappingDirection.OUTPUT.value,
            evidence="sales.orders",
        )
        repo.commit()
        prompts: list[str] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del info
            prompts.append(_user_text(messages))
            body: str = json.dumps({"context": "Nightly sales."})
            return ModelResponse(parts=[TextPart(content=body)])

        describer: AirflowContextAgent = AirflowContextAgent(
            FunctionModel(respond),
            max_requests=2,
            temperature=0.0,
            max_tokens=256,
            timeout_seconds=5.0,
        )
        text: str = (
            build_airflow_agent_factory(
                PostgresAirflowCatalogContextProvider(
                    repo,
                    RegistryRepository(session),
                    AirflowKnowledgeContextStore(session),
                ),
                describer=describer,
                context_writer=SemanticContextStore(session),
            )
            .create(instance_id)
            .describe()
        )
        assert text == "Nightly sales."
        row: AirflowInstanceRow | None = repo.get_instance(instance_id)
        assert row is not None
        assert row.context == "Nightly sales."
        assert row.context_origin == ContextOrigin.LLM.value
        assert len(prompts) == 1
        assert "source_code" in prompts[0]
        assert "writer.append_order()" in prompts[0]
        assert "write_order" in prompts[0]
        assert "polaris.sales.orders" in prompts[0]
    finally:
        session.close()
        database.dispose()


def _table(session: Session) -> UUID:
    service: OpenRegistry = open_registry(session)
    source_id: UUID = service.sources.create_data_source(
        DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST),
    ).id
    table_id: UUID = service.tables.create_table(
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
    return table_id


def _instance(repo: AirflowRepository, name: str, base_url: str) -> UUID:
    row = repo.add_instance(
        name=name,
        slug=name.lower(),
        base_url=base_url,
        username="admin",
        password="secret",
        verify_tls=True,
        description=None,
        status=AirflowInstanceStatus.ACTIVE.value,
    )
    return row.id


def _dag(
    repo: AirflowRepository,
    instance_id: UUID,
    dag_id: str,
    *,
    source_code: str | None = None,
    tasks: list[dict[str, object]] | None = None,
) -> UUID:
    stored_tasks: list[dict[str, object]] = [] if tasks is None else tasks
    row = repo.upsert_dag(
        instance_id,
        DagWrite(
            dag_id=dag_id,
            display_name=dag_id,
            description=f"{dag_id} description",
            is_paused=False,
            is_stale=False,
            has_import_errors=False,
            timetable_summary=None,
            timetable_description=None,
            owners=[],
            tags=[],
            fileloc=None,
            bundle_name=None,
            max_active_runs=None,
            next_dagrun_run_after=None,
            last_parsed_time=None,
            tasks=stored_tasks,
            last_run=None,
            run_stats=[],
            source_code=source_code,
            source_version=None,
        ),
    )
    return row.id


def _database(tmp_path: Path) -> CatalogDatabase:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    return CatalogDatabase(url)


def _user_text(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    message: ModelMessage
    for message in messages:
        part: object
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                chunks.append(part.content)
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
            elif isinstance(part, UserPromptPart) and isinstance(part.content, str):
                if seen_user:
                    chunks.append(part.content)
                seen_user = True
    return "\n".join(chunks)
