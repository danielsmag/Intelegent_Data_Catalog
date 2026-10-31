"""Airflow instance agent: one model call per instance, LLM links, stored findings."""

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock
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
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.airflow.repository import AirflowRepository, DagWrite, LlmMappingWrite
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.core.settings import (
    AppSettings,
    LlmSettings,
    MarquezSettings,
    Neo4jSettings,
    TrinoSettings,
)
from intelegent_data_catalog.exceptions import LlmUnavailableError
from intelegent_data_catalog.knowledge.context.airflow_catalog import (
    PostgresAirflowCatalogContextProvider,
)
from intelegent_data_catalog.knowledge.context.airflow_graph import AirflowGraphBuilder
from intelegent_data_catalog.knowledge.context.pipelines import (
    AttachedPipelineReader,
    AttachedPipelineRunner,
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
    PipelineInput,
    PipelineSummary,
    ScopeAnalysisContext,
    ScopeSummary,
    SpaceAnalysisContext,
    SpaceSummary,
)
from intelegent_data_catalog.knowledge.store import (
    AirflowKnowledgeContextStore,
    ScopeKnowledgeContextStore,
    SemanticContextStore,
    SpaceKnowledgeContextStore,
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
from intelegent_data_catalog.persistence.models import (
    AirflowDagTableRow,
    AirflowInstanceRow,
    ScopeKnowledgeContextRow,
    SpaceKnowledgeContextRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    CatalogTableCreate,
    DataSourceCreate,
    ScopeCreate,
    ScopeRead,
    SourceRef,
    SpaceCreate,
    SpaceRead,
)
from tests.registry_factory import OpenRegistry, open_registry


def test_airflow_agent_retries_an_unknown_table() -> None:
    """A table id outside the candidate list is rejected, then a real one is kept."""
    dag_row_id: UUID = uuid4()
    table_id: UUID = uuid4()
    scope_id: UUID = uuid4()
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
                scope_id=scope_id,
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
        created: tuple[UUID, UUID, UUID] = _scope_table(session)
        table_id: UUID = created[1]
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
        scope_id: UUID
        table_id: UUID
        scope_id, table_id, _space_id = _scope_table(session)
        del scope_id, _space_id
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


def test_runner_calls_the_model_once_per_instance(tmp_path: Path) -> None:
    """Two instances are two calls. Two DAGs on one instance are one call."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        scope_id: UUID
        table_id: UUID
        space_id: UUID
        scope_id, table_id, space_id = _scope_table(session)
        repo: AirflowRepository = AirflowRepository(session)
        first: UUID = _instance(repo, "Alpha", "http://alpha.test")
        second: UUID = _instance(repo, "Beta", "http://beta.test")
        _dag(repo, first, "alpha_one")
        _dag(repo, second, "beta_one")
        repo.add_attachment(instance_id=first, dag_row_id=None, space_id=None, scope_id=scope_id)
        repo.add_attachment(instance_id=second, dag_row_id=None, space_id=None, scope_id=scope_id)
        repo.commit()
        calls: list[str] = []
        pipelines: list[PipelineInput] = _run(session, repo, scope_id, {table_id}, calls)
        assert len(calls) == 2
        assert {item.instance_name for item in pipelines} == {"Alpha", "Beta"}
        assert {item.instance_summary for item in pipelines} == {"Pipelines."}

        same: UUID = _instance(repo, "Same", "http://same.test")
        _dag(repo, same, "same_a")
        _dag(repo, same, "same_b")
        other_scope: UUID = (
            open_registry(session)
            .scopes.create_scope(
                space_id,
                ScopeCreate(name="Billing"),
            )
            .id
        )
        repo.add_attachment(instance_id=same, dag_row_id=None, space_id=None, scope_id=other_scope)
        repo.commit()
        again: list[str] = []
        _run(session, repo, other_scope, {table_id}, again)
        assert len(again) == 1
        assert "same_a" in again[0]
        assert "same_b" in again[0]
    finally:
        session.close()
        database.dispose()


def test_runner_keeps_a_scope_build_when_one_instance_fails(tmp_path: Path) -> None:
    """A model failure for one instance falls back to the stored finding."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        scope_id: UUID
        table_id: UUID
        scope_id, table_id, _space_id = _scope_table(session)
        del _space_id
        repo: AirflowRepository = AirflowRepository(session)
        broken_id: UUID = _instance(repo, "Broken", "http://broken.test")
        healthy_id: UUID = _instance(repo, "Healthy", "http://healthy.test")
        broken_dag: UUID = _dag(repo, broken_id, "broken_job")
        _dag(repo, healthy_id, "healthy_job")
        repo.add_attachment(
            instance_id=broken_id,
            dag_row_id=None,
            space_id=None,
            scope_id=scope_id,
        )
        repo.add_attachment(
            instance_id=healthy_id,
            dag_row_id=None,
            space_id=None,
            scope_id=scope_id,
        )
        repo.commit()
        AirflowKnowledgeContextStore(session).save(
            AirflowAnalysisContext(
                instance_id=broken_id,
                instance_name="Broken",
                summary="Stored.",
                findings=[
                    DagFinding(
                        dag_row_id=broken_dag,
                        dag_id="broken_job",
                        summary="Saved finding.",
                    ),
                ],
                dags_analyzed=1,
            )
        )

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del info
            text: str = _user_text(messages)
            if "Broken" in text:
                raise LlmUnavailableError("down")
            return ModelResponse(parts=[TextPart(content='{"summary":"Healthy runs.","dags":[]}')])

        pipelines: list[PipelineInput] = _run(
            session,
            repo,
            scope_id,
            {table_id},
            [],
            respond,
        )
        by_name: dict[str, str | None] = {item.instance_name: item.summary for item in pipelines}
        by_instance: dict[str, str | None] = {
            item.instance_name: item.instance_summary for item in pipelines
        }
        assert by_name["Broken"] == "Saved finding."
        assert by_instance["Broken"] == "Stored."
        assert by_instance["Healthy"] == "Healthy runs."
        assert "healthy_job" in {item.dag_id for item in pipelines}
    finally:
        session.close()
        database.dispose()


def test_reader_copies_instance_context(tmp_path: Path) -> None:
    """Stored Airflow instance context is attached to each pipeline for the scope."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        scope_id: UUID
        table_id: UUID
        scope_id, table_id, _space_id = _scope_table(session)
        del _space_id
        repo: AirflowRepository = AirflowRepository(session)
        instance_id: UUID = _instance(repo, "Iceberg", "http://airflow.test")
        _dag(repo, instance_id, "append_sales_order")
        repo.add_attachment(
            instance_id=instance_id,
            dag_row_id=None,
            space_id=None,
            scope_id=scope_id,
        )
        row: AirflowInstanceRow | None = repo.get_instance(instance_id)
        assert row is not None
        row.context = "Nightly sales loads."
        repo.commit()
        reader: AttachedPipelineReader = AttachedPipelineReader(
            repo,
            AirflowKnowledgeContextStore(session),
        )
        found: list[PipelineInput] = reader.for_scope(scope_id, {table_id})
        assert len(found) == 1
        assert found[0].dag_id == "append_sales_order"
        assert found[0].instance_context == "Nightly sales loads."
    finally:
        session.close()
        database.dispose()


def test_reader_copies_instance_summary(tmp_path: Path) -> None:
    """A stored Airflow instance summary is copied onto every pipeline for that instance."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        scope_id: UUID
        table_id: UUID
        scope_id, table_id, _space_id = _scope_table(session)
        del _space_id
        repo: AirflowRepository = AirflowRepository(session)
        instance_id: UUID = _instance(repo, "Iceberg", "http://airflow.test")
        blank_id: UUID = _instance(repo, "Quiet", "http://quiet.test")
        first: UUID = _dag(repo, instance_id, "append_sales_order")
        second: UUID = _dag(repo, instance_id, "append_sales_customer")
        _dag(repo, blank_id, "quiet_job")
        repo.add_attachment(
            instance_id=instance_id,
            dag_row_id=None,
            space_id=None,
            scope_id=scope_id,
        )
        repo.add_attachment(
            instance_id=blank_id,
            dag_row_id=None,
            space_id=None,
            scope_id=scope_id,
        )
        repo.commit()
        AirflowKnowledgeContextStore(session).save(
            AirflowAnalysisContext(
                instance_id=instance_id,
                instance_name="Iceberg",
                summary="Nightly sales loads land in Iceberg.",
                findings=[
                    DagFinding(dag_row_id=first, dag_id="append_sales_order", summary="Orders."),
                    DagFinding(
                        dag_row_id=second,
                        dag_id="append_sales_customer",
                        summary="Customers.",
                    ),
                ],
                dags_analyzed=2,
            )
        )
        AirflowKnowledgeContextStore(session).save(
            AirflowAnalysisContext(
                instance_id=blank_id,
                instance_name="Quiet",
                summary="  ",
                findings=[],
                dags_analyzed=0,
            )
        )
        found: list[PipelineInput] = AttachedPipelineReader(
            repo,
            AirflowKnowledgeContextStore(session),
        ).for_scope(scope_id, {table_id})
        by_dag: dict[str, str | None] = {item.dag_id: item.instance_summary for item in found}
        assert by_dag["append_sales_order"] == "Nightly sales loads land in Iceberg."
        assert by_dag["append_sales_customer"] == "Nightly sales loads land in Iceberg."
        assert by_dag["quiet_job"] is None
    finally:
        session.close()
        database.dispose()


def test_read_fills_a_snapshot_missing_the_airflow_summary(tmp_path: Path) -> None:
    """An older scope or space snapshot picks up the stored Airflow summary on read."""
    database: CatalogDatabase = _database(tmp_path)
    session: Session = database.session()
    try:
        scope_id: UUID
        space_id: UUID
        scope_id, _table_id, space_id = _scope_table(session)
        del _table_id
        service: OpenRegistry = open_registry(session)
        scope: ScopeRead = service.scopes.get_scope(scope_id)
        space: SpaceRead = service.spaces.get_space(space_id)
        repo: AirflowRepository = AirflowRepository(session)
        instance_id: UUID = _instance(repo, "Iceberg", "http://airflow.test")
        repo.commit()
        AirflowKnowledgeContextStore(session).save(
            AirflowAnalysisContext(
                instance_id=instance_id,
                instance_name="Iceberg",
                summary="Nightly sales loads land in Iceberg.",
                findings=[],
                dags_analyzed=0,
            )
        )
        scope_model: ScopeSummary = ScopeSummary(
            id=scope.id,
            space_id=scope.space_id,
            name=scope.name,
            slug=scope.slug,
            description=scope.description,
            context=scope.context,
        )
        space_model: SpaceSummary = SpaceSummary(
            id=space.id,
            name=space.name,
            slug=space.slug,
            description=space.description,
            context=space.context,
        )
        ScopeKnowledgeContextStore(session).save(
            ScopeAnalysisContext(
                scope=scope_model,
                space=space_model,
                tables=[],
                existing_relationships=[],
                pipelines=[
                    PipelineSummary(
                        dag_id="append_sales_customer",
                        instance_name="Iceberg",
                        instance_summary="Keep this.",
                    ),
                    PipelineSummary(dag_id="append_sales_order", instance_name="Iceberg"),
                    PipelineSummary(dag_id="other_job", instance_name="Other"),
                ],
            )
        )
        SpaceKnowledgeContextStore(session).save(
            SpaceAnalysisContext(
                space=space_model,
                scopes=[],
                existing_relationships=[],
                pipelines=[PipelineSummary(dag_id="nightly", instance_name="Iceberg")],
            )
        )
        scope_row: ScopeKnowledgeContextRow | None = session.scalar(
            select(ScopeKnowledgeContextRow).where(ScopeKnowledgeContextRow.scope_id == scope_id)
        )
        space_row: SpaceKnowledgeContextRow | None = session.scalar(
            select(SpaceKnowledgeContextRow).where(SpaceKnowledgeContextRow.space_id == space_id)
        )
        assert scope_row is not None
        assert space_row is not None
        _omit_instance_summary(session, scope_row)
        _omit_instance_summary(session, space_row)
        platform: RequestPlatform = RequestPlatform(
            session,
            AppSettings(
                llm=LlmSettings(),
                neo4j=Neo4jSettings(enabled=False),
                marquez=MarquezSettings(enabled=False),
                trino=TrinoSettings(enabled=False),
            ),
            MagicMock(spec=QueryService),
        )
        loaded_scope: ScopeAnalysisContext = platform.read_scope_context(scope_id)
        by_dag: dict[str, str | None] = {
            item.dag_id: item.instance_summary for item in loaded_scope.pipelines
        }
        assert by_dag["append_sales_customer"] == "Keep this."
        assert by_dag["append_sales_order"] == "Nightly sales loads land in Iceberg."
        assert by_dag["other_job"] is None
        loaded_space: SpaceAnalysisContext = platform.read_space_context(space_id)
        assert loaded_space.pipelines[0].instance_summary == "Nightly sales loads land in Iceberg."
        stored: ScopeAnalysisContext | None = ScopeKnowledgeContextStore(session).get(scope_id)
        assert stored is not None
        stored_order: PipelineSummary = stored.pipelines[1]
        assert stored_order.dag_id == "append_sales_order"
        assert stored_order.instance_summary is None
    finally:
        session.close()
        database.dispose()


def _omit_instance_summary(
    session: Session,
    row: ScopeKnowledgeContextRow | SpaceKnowledgeContextRow,
) -> None:
    payload: dict[str, object] = dict(row.payload)
    raw: object = payload.get("pipelines")
    if not isinstance(raw, list):
        return
    cleaned: list[object] = []
    item: object
    for item in raw:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        copied: dict[str, object] = dict(item)
        if copied.get("instance_summary") is None:
            copied.pop("instance_summary", None)
        cleaned.append(copied)
    payload["pipelines"] = cleaned
    row.payload = payload
    session.commit()


def _run(
    session: Session,
    repo: AirflowRepository,
    scope_id: UUID,
    table_ids: set[UUID],
    calls: list[str],
    respond: Callable[[list[ModelMessage], AgentInfo], ModelResponse] | None = None,
) -> list[PipelineInput]:
    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        calls.append(_user_text(messages))
        return ModelResponse(parts=[TextPart(content='{"summary":"Pipelines.","dags":[]}')])

    chosen: Callable[[list[ModelMessage], AgentInfo], ModelResponse] = (
        _respond if respond is None else respond
    )
    agent: AirflowAnalysisAgent = AirflowAnalysisAgent(
        FunctionModel(chosen),
        max_requests=2,
        temperature=0.0,
        max_tokens=256,
        timeout_seconds=5.0,
    )
    factory = build_airflow_agent_factory(
        PostgresAirflowCatalogContextProvider(
            repo,
            RegistryRepository(session),
            AirflowKnowledgeContextStore(session),
        ),
        AirflowGraphBuilder(agent, repo),
    )
    runner: AttachedPipelineRunner = AttachedPipelineRunner(
        repo,
        factory,
        AirflowKnowledgeContextStore(session),
    )
    return runner.for_scope(scope_id, table_ids)


def _scope_table(session: Session) -> tuple[UUID, UUID, UUID]:
    service: OpenRegistry = open_registry(session)
    space_id: UUID = service.spaces.create_space(SpaceCreate(name="Commerce")).id
    scope_id: UUID = service.scopes.create_scope(space_id, ScopeCreate(name="Ordering")).id
    source_id: UUID = service.sources.create_data_source(
        DataSourceCreate(name="Lake", type=DataSourceType.ICEBERG_REST),
    ).id
    table_id: UUID = service.tables.create_table(
        scope_id,
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
    return scope_id, table_id, space_id


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
