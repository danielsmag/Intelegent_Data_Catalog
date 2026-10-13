"""Structured model call that explains one Airflow instance's DAGs and their tables."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.context.airflow_graph import accept_airflow_findings
from intelegent_data_catalog.knowledge.models import (
    AirflowCatalogContext,
    AirflowDagInput,
    AirflowProposal,
    DagCandidateTable,
    DagFinding,
    DagTableLink,
    DagTableRef,
    DagTaskSummary,
    PreviousAirflowFindings,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

_INSTRUCTIONS: str = (
    "You analyze one Airflow instance. "
    "The user message is JSON with the instance and every DAG to analyze in this call. "
    "Return one finding per DAG you were given, plus a short instance summary. "
    "Each finding says what that pipeline does in one or two sentences. "
    "Link a catalog table only when the DAG source or task names show the DAG reads or writes it. "
    "INPUT means the DAG reads the table. OUTPUT means the DAG writes the table. "
    "UNKNOWN means the direction is not clear. "
    "Use table ids from candidate_tables only. "
    "Use dag_row_id and dag_id from the DAG you were given. "
    "known_tables are links already stored. Keep a justified one and drop the rest. "
    "previous_findings are a draft for these DAGs: keep, drop, or add. "
    "Do not invent DAG ids or table ids. "
    "Return no links for a DAG when the source does not name a candidate table. "
    "The instance summary ties the DAGs together in two to four sentences."
)


class _PromptTask(BaseModel):
    """One DAG task sent to the model."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    operator_name: str | None = None
    downstream_task_ids: list[str] = []


class _PromptKnownTable(BaseModel):
    """Table already linked to a DAG."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    qualified_name: str
    direction: str
    origin: str


class _PromptDag(BaseModel):
    """One DAG inside the Airflow prompt."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    dag_id: str
    display_name: str | None
    description: str | None
    schedule: str | None
    owners: list[str]
    tags: list[str]
    tasks: list[_PromptTask]
    source_code: str | None
    known_tables: list[_PromptKnownTable]


class _PromptCandidate(BaseModel):
    """Catalog table the model may link."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    qualified_name: str
    description: str | None


class _PromptLink(BaseModel):
    """One link from a previous finding."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    direction: str
    reason: str


class _PromptFinding(BaseModel):
    """Draft finding for one DAG."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    dag_id: str
    summary: str
    links: list[_PromptLink]


class _PromptPrevious(BaseModel):
    """Draft the model may keep, drop, or extend."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None
    findings: list[_PromptFinding]


class _AirflowPrompt(BaseModel):
    """User message for one Airflow instance call."""

    model_config = ConfigDict(extra="forbid")

    instance_id: UUID
    instance_name: str
    instance_description: str | None
    dags: list[_PromptDag]
    candidate_tables: list[_PromptCandidate]
    previous_findings: _PromptPrevious | None


class AirflowAnalysisAgent(ProposalAgent[AirflowCatalogContext, AirflowProposal]):
    """Asks the model what each DAG in one Airflow instance does."""

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._call: ModelCall[AirflowCatalogContext, AirflowProposal] = ModelCall(
            _airflow_agent(model),
            name="airflow-analysis",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=False,
        )

    def propose(self, context: AirflowCatalogContext) -> AirflowProposal:
        """Return the summary and per-DAG findings the model selected."""
        return self._call.run(_prompt(context), context)


def _airflow_agent(model: Model) -> Agent[AirflowCatalogContext, AirflowProposal]:
    """JSON schema. A tool call lets Qwen think until the answer is empty."""
    agent: Agent[AirflowCatalogContext, AirflowProposal] = Agent(
        model,
        deps_type=AirflowCatalogContext,
        output_type=structured_output(model, AirflowProposal),
        name="airflow-analysis",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    agent.output_validator(_accept_output)
    return agent


def _accept_output(
    ctx: RunContext[AirflowCatalogContext],
    proposed: AirflowProposal,
) -> AirflowProposal:
    try:
        accept_airflow_findings(ctx.deps, proposed.dags)
    except ValidationAppError as err:
        raise ModelRetry(str(err)) from err
    return proposed


def _prompt(context: AirflowCatalogContext) -> str:
    dags: list[_PromptDag] = [_dag(dag) for dag in context.dags]
    candidates: list[_PromptCandidate] = [_candidate(table) for table in context.candidate_tables]
    payload: _AirflowPrompt = _AirflowPrompt(
        instance_id=context.instance_id,
        instance_name=context.instance_name,
        instance_description=context.instance_description,
        dags=dags,
        candidate_tables=candidates,
        previous_findings=_previous(context.previous),
    )
    rendered: str = payload.model_dump_json(exclude_none=True)
    return rendered


def _dag(dag: AirflowDagInput) -> _PromptDag:
    tasks: list[_PromptTask] = [_task(task) for task in dag.tasks]
    known: list[_PromptKnownTable] = [_known(table) for table in dag.known_tables]
    return _PromptDag(
        dag_row_id=dag.dag_row_id,
        dag_id=dag.dag_id,
        display_name=dag.display_name,
        description=dag.description,
        schedule=dag.schedule,
        owners=list(dag.owners),
        tags=list(dag.tags),
        tasks=tasks,
        source_code=dag.source_code,
        known_tables=known,
    )


def _task(task: DagTaskSummary) -> _PromptTask:
    return _PromptTask(
        task_id=task.task_id,
        operator_name=task.operator_name,
        downstream_task_ids=list(task.downstream_task_ids),
    )


def _known(table: DagTableRef) -> _PromptKnownTable:
    return _PromptKnownTable(
        table_id=table.table_id,
        qualified_name=table.qualified_name,
        direction=table.direction.value,
        origin=table.origin.value,
    )


def _candidate(table: DagCandidateTable) -> _PromptCandidate:
    return _PromptCandidate(
        table_id=table.id,
        qualified_name=table.qualified_name,
        description=table.description,
    )


def _previous(previous: PreviousAirflowFindings | None) -> _PromptPrevious | None:
    if previous is None:
        return None
    findings: list[_PromptFinding] = []
    finding: DagFinding
    for finding in previous.findings:
        links: list[_PromptLink] = []
        link: DagTableLink
        for link in finding.links:
            links.append(
                _PromptLink(
                    table_id=link.table_id,
                    direction=link.direction.value,
                    reason=link.reason,
                )
            )
        findings.append(
            _PromptFinding(
                dag_row_id=finding.dag_row_id,
                dag_id=finding.dag_id,
                summary=finding.summary,
                links=links,
            )
        )
    return _PromptPrevious(summary=previous.summary, findings=findings)
