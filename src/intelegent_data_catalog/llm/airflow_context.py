"""Structured model call that writes business context for one Airflow instance."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.settings import ThinkingLevel

from intelegent_data_catalog.knowledge.models import (
    AirflowCatalogContext,
    AirflowContextProposal,
    AirflowDagInput,
    DagTableRef,
    DagTaskSummary,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

_INSTRUCTIONS: str = (
    "You write business context for one Airflow instance. "
    "The user message is JSON with the instance and its DAGs. "
    "source_code, tasks, and known_tables are the DAG body. Use them. "
    "Return two to four sentences on what this deployment runs and how the pipelines fit together. "
    "Do not invent DAGs. "
    "Do not invent tables the source does not name. "
    "Do not invent facts that the names, descriptions, source, or tasks do not support."
)


class _PromptTask(BaseModel):
    """One DAG task sent with the instance."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    operator_name: str | None = None


class _PromptKnownTable(BaseModel):
    """Catalog table already linked to a DAG."""

    model_config = ConfigDict(extra="forbid")

    qualified_name: str
    direction: str


class _PromptDag(BaseModel):
    """One DAG inside an Airflow context prompt."""

    model_config = ConfigDict(extra="forbid")

    dag_id: str
    description: str | None
    schedule: str | None
    tasks: list[_PromptTask]
    source_code: str | None
    known_tables: list[_PromptKnownTable]


class _AirflowPrompt(BaseModel):
    """User message for one Airflow instance."""

    model_config = ConfigDict(extra="forbid")

    instance_id: UUID
    instance_name: str
    description: str | None
    existing_context: str | None
    dags: list[_PromptDag]


class AirflowContextAgent(ProposalAgent[AirflowCatalogContext, AirflowContextProposal]):
    """Asks the model for one Airflow instance's business context."""

    def __init__(
        self,
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        thinking: ThinkingLevel = False,
    ) -> None:
        self._call: ModelCall[AirflowCatalogContext, AirflowContextProposal] = ModelCall(
            _airflow_agent(model),
            name="airflow-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )

    def propose(self, context: AirflowCatalogContext) -> AirflowContextProposal:
        """Return the context the model wrote for the instance."""
        return self._call.run(_prompt(context).model_dump_json(exclude_none=True), context)


def _airflow_agent(model: Model) -> Agent[AirflowCatalogContext, AirflowContextProposal]:
    """JSON schema. A tool call lets Qwen think until the answer is empty."""
    agent: Agent[AirflowCatalogContext, AirflowContextProposal] = Agent(
        model,
        deps_type=AirflowCatalogContext,
        output_type=structured_output(model, AirflowContextProposal),
        name="airflow-context",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    return agent


def _prompt(context: AirflowCatalogContext) -> _AirflowPrompt:
    dags: list[_PromptDag] = [_dag(dag) for dag in context.dags]
    return _AirflowPrompt(
        instance_id=context.instance_id,
        instance_name=context.instance_name,
        description=context.instance_description,
        existing_context=context.instance_context,
        dags=dags,
    )


def _dag(dag: AirflowDagInput) -> _PromptDag:
    tasks: list[_PromptTask] = [_task(task) for task in dag.tasks]
    known: list[_PromptKnownTable] = [_known(table) for table in dag.known_tables]
    return _PromptDag(
        dag_id=dag.dag_id,
        description=dag.description,
        schedule=dag.schedule,
        tasks=tasks,
        source_code=dag.source_code,
        known_tables=known,
    )


def _task(task: DagTaskSummary) -> _PromptTask:
    return _PromptTask(task_id=task.task_id, operator_name=task.operator_name)


def _known(table: DagTableRef) -> _PromptKnownTable:
    return _PromptKnownTable(
        qualified_name=table.qualified_name,
        direction=table.direction.value,
    )
