"""Airflow instance asset. Context builds go through the instance agent."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import Asset, AssetAgent
from intelegent_data_catalog.assets.models import AssetAnalysisContext, AssetContextOptions
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.knowledge.models import AirflowAnalysisContext, AirflowContextOptions
from intelegent_data_catalog.knowledge.operations.airflow_operation import AirflowInstanceAgent
from intelegent_data_catalog.knowledge.store import AirflowKnowledgeContextStore
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AssetKind
from intelegent_data_catalog.persistence.models import AirflowInstanceRow

_INSTANCE_OPTIONS: dict[str, bool] = {
    "include_source": True,
    "include_previous": True,
}


class InstanceAssetDetail(BaseModel):
    """Registered Airflow instance behind an instance asset."""

    model_config = ConfigDict(extra="forbid")

    instance_id: UUID
    slug: str
    status: str


class InstanceAssetAgentAdapter(AssetAgent):
    """Maps asset build and describe calls onto ``AirflowInstanceAgent``."""

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

    def build_context(self, options: AssetContextOptions) -> AssetAnalysisContext:
        """Analyze every synced DAG and store the instance snapshot."""
        flags: dict[str, bool] = options.flags
        built: AirflowAnalysisContext = self._agent.build_context(
            AirflowContextOptions(
                include_source=flags.get("include_source", True),
                include_previous=flags.get("include_previous", True),
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
        asset_id: UUID | None = None if instance is None else instance.asset_id
        if asset_id is None:
            raise EntityNotFoundError("asset", str(self._instance_id))
        summary: str | None = built.summary.strip() if built.summary else None
        if summary == "":
            summary = None
        return AssetAnalysisContext(
            asset_id=asset_id,
            kind=AssetKind.AIRFLOW_INSTANCE,
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


class InstanceAsset(Asset):
    """One registered Airflow instance addressed as an asset."""

    def default_options(self) -> dict[str, bool]:
        """Flags for an instance context build."""
        return dict(_INSTANCE_OPTIONS)

    def agent(self) -> AssetAgent:
        """Adapter over the instance agent."""
        instance: AirflowInstanceRow = self._instance()
        return self._agents.for_instance(instance.id)

    def detail(self) -> InstanceAssetDetail:
        """Instance id, slug, and registry status."""
        instance: AirflowInstanceRow = self._instance()
        return InstanceAssetDetail(
            instance_id=instance.id,
            slug=instance.slug,
            status=instance.status,
        )

    def _instance(self) -> AirflowInstanceRow:
        statement = select(AirflowInstanceRow).where(AirflowInstanceRow.asset_id == self.id)
        instance: AirflowInstanceRow | None = self._session.scalar(statement)
        if instance is None:
            raise EntityNotFoundError("airflow instance", str(self.id))
        return instance
