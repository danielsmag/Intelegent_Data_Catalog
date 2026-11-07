"""Airflow instance kind. Context builds go through the instance agent."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.base import ContextBuilder, ContextBuilderProvider
from intelegent_data_catalog.agents.models import AgentAnalysisContext, AgentContextOptions
from intelegent_data_catalog.agents.strategies.flags import INSTANCE_FLAGS
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.knowledge.models import AirflowAnalysisContext, AirflowContextOptions
from intelegent_data_catalog.knowledge.operations.airflow_operation import AirflowInstanceAgent
from intelegent_data_catalog.knowledge.store import AirflowKnowledgeContextStore
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AgentKind
from intelegent_data_catalog.persistence.models import AirflowInstanceRow


class InstanceAgentDetail(BaseModel):
    """Registered Airflow instance behind an instance agent."""

    model_config = ConfigDict(extra="forbid")

    instance_id: UUID
    slug: str
    status: str


class InstanceContextBuilder(ContextBuilder):
    """Maps agent build and describe calls onto ``AirflowInstanceAgent``."""

    def __init__(
        self,
        instance_id: UUID,
        agent: AirflowInstanceAgent,
        session: Session,
    ) -> None:
        self._instance_id: UUID = instance_id
        self._agent: AirflowInstanceAgent = agent
        self._session: Session = session

    def get_metadata(self) -> BaseModel:
        """Load the instance catalog context."""
        return self._agent.get_metadata()

    def build_context(self, options: AgentContextOptions) -> AgentAnalysisContext:
        """Analyze every synced DAG and store the instance snapshot."""
        flags: dict[str, bool] = options.flags
        built: AirflowAnalysisContext = self._agent.build_context(
            AirflowContextOptions(
                include_source=flags.get("include_source", INSTANCE_FLAGS["include_source"]),
                include_previous=flags.get(
                    "include_previous",
                    INSTANCE_FLAGS["include_previous"],
                ),
                include_summary=True,
                model=options.model,
                effort=options.effort,
            ),
        )
        AirflowKnowledgeContextStore(self._session).save(built)
        instance: AirflowInstanceRow | None = self._session.get(
            AirflowInstanceRow,
            self._instance_id,
        )
        agent_id: UUID | None = None if instance is None else instance.agent_id
        if agent_id is None:
            raise EntityNotFoundError("agent", str(self._instance_id))
        summary: str | None = built.summary.strip() if built.summary else None
        if summary == "":
            summary = None
        return AgentAnalysisContext(
            agent_id=agent_id,
            kind=AgentKind.AIRFLOW_INSTANCE,
            summary=summary,
            built_at=utc_now(),
            payload={
                "instance_id": str(self._instance_id),
                "dags_analyzed": built.dags_analyzed,
                "findings": len(built.findings),
            },
        )

    def describe(self) -> str:
        """Write the instance business context through the instance agent."""
        return self._agent.describe()


class InstanceStrategy:
    """One registered Airflow instance addressed through one agent id."""

    def default_flags(self) -> dict[str, bool]:
        """Flags for an instance context build."""
        return dict(INSTANCE_FLAGS)

    def builder(
        self,
        session: Session,
        agents: ContextBuilderProvider,
        agent_id: UUID,
    ) -> ContextBuilder:
        """Adapter over the instance agent."""
        instance: AirflowInstanceRow = _instance(session, agent_id)
        return agents.for_instance(instance.id)

    def detail(self, session: Session, agent_id: UUID) -> InstanceAgentDetail:
        """Instance id, slug, and registry status."""
        instance: AirflowInstanceRow = _instance(session, agent_id)
        return InstanceAgentDetail(
            instance_id=instance.id,
            slug=instance.slug,
            status=instance.status,
        )


def _instance(session: Session, agent_id: UUID) -> AirflowInstanceRow:
    instance: AirflowInstanceRow | None = session.scalar(
        select(AirflowInstanceRow).where(AirflowInstanceRow.agent_id == agent_id),
    )
    if instance is None:
        raise EntityNotFoundError("airflow instance", str(agent_id))
    return instance
