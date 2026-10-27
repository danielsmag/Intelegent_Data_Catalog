"""Airflow DAG kind."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import AssetAgent, AssetAgentProvider
from intelegent_data_catalog.assets.strategies.flags import DAG_FLAGS
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.models import AirflowDagRow


class DagAssetDetail(BaseModel):
    """Synced DAG behind a DAG asset."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    instance_id: UUID
    dag_id: str
    is_paused: bool
    is_stale: bool


class DagStrategy:
    """One Airflow DAG addressed through one asset id."""

    def default_flags(self) -> dict[str, bool]:
        """Flags for a DAG context build."""
        return dict(DAG_FLAGS)

    def agent(
        self,
        session: Session,
        agents: AssetAgentProvider,
        asset_id: UUID,
    ) -> AssetAgent:
        """Per-DAG agent over the instance agent."""
        dag: AirflowDagRow = _dag(session, asset_id)
        opened: AssetAgent = agents.for_dag(dag.id, dag.instance_id)
        return opened

    def detail(self, session: Session, asset_id: UUID) -> DagAssetDetail:
        """DAG identity."""
        dag: AirflowDagRow = _dag(session, asset_id)
        return DagAssetDetail(
            dag_row_id=dag.id,
            instance_id=dag.instance_id,
            dag_id=dag.dag_id,
            is_paused=dag.is_paused,
            is_stale=dag.is_stale,
        )


def _dag(session: Session, asset_id: UUID) -> AirflowDagRow:
    dag: AirflowDagRow | None = session.scalar(
        select(AirflowDagRow).where(AirflowDagRow.asset_id == asset_id),
    )
    if dag is None:
        raise EntityNotFoundError("airflow dag", str(asset_id))
    return dag
