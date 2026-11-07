"""Airflow DAG kind."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.base import ContextBuilder, ContextBuilderProvider
from intelegent_data_catalog.agents.strategies.flags import DAG_FLAGS
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.models import AirflowDagRow


class DagAgentDetail(BaseModel):
    """Synced DAG behind a DAG agent."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    instance_id: UUID
    dag_id: str
    is_paused: bool
    is_stale: bool


class DagStrategy:
    """One Airflow DAG addressed through one agent id."""

    def default_flags(self) -> dict[str, bool]:
        """Flags for a DAG context build."""
        return dict(DAG_FLAGS)

    def builder(
        self,
        session: Session,
        agents: ContextBuilderProvider,
        agent_id: UUID,
    ) -> ContextBuilder:
        """Per-DAG agent over the instance agent."""
        dag: AirflowDagRow = _dag(session, agent_id)
        opened: ContextBuilder = agents.for_dag(dag.id, dag.instance_id)
        return opened

    def detail(self, session: Session, agent_id: UUID) -> DagAgentDetail:
        """DAG identity."""
        dag: AirflowDagRow = _dag(session, agent_id)
        return DagAgentDetail(
            dag_row_id=dag.id,
            instance_id=dag.instance_id,
            dag_id=dag.dag_id,
            is_paused=dag.is_paused,
            is_stale=dag.is_stale,
        )


def _dag(session: Session, agent_id: UUID) -> AirflowDagRow:
    dag: AirflowDagRow | None = session.scalar(
        select(AirflowDagRow).where(AirflowDagRow.agent_id == agent_id),
    )
    if dag is None:
        raise EntityNotFoundError("airflow dag", str(agent_id))
    return dag
