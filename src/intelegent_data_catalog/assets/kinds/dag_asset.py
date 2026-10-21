"""Airflow DAG asset."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from intelegent_data_catalog.assets.base import Asset, AssetAgent
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.models import AirflowDagRow

_DAG_OPTIONS: dict[str, bool] = {
    "include_source": True,
    "include_previous": True,
}


class DagAssetDetail(BaseModel):
    """Synced DAG behind a DAG asset."""

    model_config = ConfigDict(extra="forbid")

    dag_row_id: UUID
    instance_id: UUID
    dag_id: str
    is_paused: bool
    is_stale: bool


class DagAsset(Asset):
    """One Airflow DAG addressed as an asset."""

    def default_options(self) -> dict[str, bool]:
        """Flags for a DAG context build."""
        return dict(_DAG_OPTIONS)

    def agent(self) -> AssetAgent:
        """Per-DAG agent over the instance agent."""
        dag: AirflowDagRow = self._dag()
        opened: AssetAgent = self._agents.for_dag(dag.id, dag.instance_id)
        return opened

    def detail(self) -> DagAssetDetail:
        """DAG identity."""
        dag: AirflowDagRow = self._dag()
        return DagAssetDetail(
            dag_row_id=dag.id,
            instance_id=dag.instance_id,
            dag_id=dag.dag_id,
            is_paused=dag.is_paused,
            is_stale=dag.is_stale,
        )

    def _dag(self) -> AirflowDagRow:
        statement = select(AirflowDagRow).where(AirflowDagRow.asset_id == self.id)
        dag: AirflowDagRow | None = self._session.scalar(statement)
        if dag is None:
            raise EntityNotFoundError("airflow dag", str(self.id))
        return dag
