"""Agent for one Airflow DAG. The instance agent does the model call."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.agents.base import ContextBuilder
from intelegent_data_catalog.agents.models import (
    AgentAnalysisContext,
    AgentContextOptions,
    DagDescribeInput,
    FileContextProposal,
)
from intelegent_data_catalog.exceptions import LlmUnavailableError
from intelegent_data_catalog.knowledge.models import AirflowAnalysisContext, AirflowContextOptions
from intelegent_data_catalog.knowledge.operations.airflow_operation import AirflowInstanceAgent
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AgentKind
from intelegent_data_catalog.persistence.models import AirflowDagRow


class DagMetadata(BaseModel):
    """Facts loaded for one DAG without a model call."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    instance_id: UUID
    dag_id: str
    description: str | None


class AirflowDagAgent(ContextBuilder):
    """Build context for one DAG by asking the instance agent for that DAG only."""

    def __init__(
        self,
        dag: AirflowDagRow,
        instance_agent: AirflowInstanceAgent,
        describer: ProposalAgent[DagDescribeInput, FileContextProposal] | None = None,
    ) -> None:
        self._dag: AirflowDagRow = dag
        self._instance_agent: AirflowInstanceAgent = instance_agent
        self._describer: ProposalAgent[DagDescribeInput, FileContextProposal] | None = describer

    def get_metadata(self) -> DagMetadata:
        """Return the DAG identity without source text."""
        return DagMetadata(
            dag_row_id=self._dag.id,
            instance_id=self._dag.instance_id,
            dag_id=self._dag.dag_id,
            description=self._dag.description,
        )

    def build_context(self, options: AgentContextOptions) -> AgentAnalysisContext:
        """Analyze this DAG. Other DAGs of the instance are left out of the call."""
        flags: dict[str, bool] = options.flags
        built: AirflowAnalysisContext = self._instance_agent.build_context(
            AirflowContextOptions(
                include_source=flags.get("include_source", True),
                include_previous=flags.get("include_previous", True),
                include_summary=True,
                model=options.model,
                effort=options.effort,
            ),
            dag_row_ids={self._dag.id},
        )
        summary: str | None = None
        finding_count: int = 0
        for finding in built.findings:
            if finding.dag_row_id != self._dag.id:
                continue
            finding_count += 1
            text: str = finding.summary.strip()
            if text != "":
                summary = text
        agent_id: UUID | None = self._dag.agent_id
        if agent_id is None:
            raise LlmUnavailableError("dag has no agent")
        return AgentAnalysisContext(
            agent_id=agent_id,
            kind=AgentKind.AIRFLOW_DAG,
            summary=summary,
            built_at=utc_now(),
            payload={
                "instance_id": str(self._dag.instance_id),
                "dag_id": self._dag.dag_id,
                "dags_analyzed": built.dags_analyzed,
                "findings": finding_count,
            },
        )

    def describe(self) -> str:
        """Ask for this DAG's business context."""
        if self._describer is None:
            raise LlmUnavailableError("context generation is not configured")
        task_ids: list[str] = []
        task: object
        for task in self._dag.tasks:
            if isinstance(task, dict):
                task_id: object = task.get("task_id")
                if isinstance(task_id, str):
                    task_ids.append(task_id)
        proposed: FileContextProposal = self._describer.propose(
            DagDescribeInput(
                agent_id=self._dag.agent_id or self._dag.id,
                dag_id=self._dag.dag_id,
                description=self._dag.description,
                source_code=self._dag.source_code,
                task_ids=task_ids,
            ),
        )
        cleaned: str = proposed.context.strip()
        if cleaned == "":
            raise LlmUnavailableError("context generation returned an empty answer")
        return cleaned
