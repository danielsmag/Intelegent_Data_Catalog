"""Load one asset and the strategy for its stored kind."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import Asset, AssetAgentProvider
from intelegent_data_catalog.assets.strategies.dag import DagStrategy
from intelegent_data_catalog.assets.strategies.file import FileStrategy
from intelegent_data_catalog.assets.strategies.instance import InstanceStrategy
from intelegent_data_catalog.assets.strategies.protocol import KindStrategy
from intelegent_data_catalog.assets.strategies.table import TableStrategy
from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.persistence.enums import AssetKind
from intelegent_data_catalog.persistence.models import AssetRow

_STRATEGIES: dict[AssetKind, KindStrategy] = {
    AssetKind.TABLE: TableStrategy(),
    AssetKind.AIRFLOW_DAG: DagStrategy(),
    AssetKind.AIRFLOW_INSTANCE: InstanceStrategy(),
    AssetKind.NOTEBOOK: FileStrategy(notebook=True),
    AssetKind.FILE: FileStrategy(notebook=False),
}


class AssetRegistry:
    """Maps ``AssetKind`` to the strategy that owns that kind's agent."""

    def __init__(self, session: Session, agents: AssetAgentProvider) -> None:
        self._session: Session = session
        self._agents: AssetAgentProvider = agents

    def load(self, asset_id: UUID) -> Asset:
        """Return the asset. Missing ids raise."""
        row: AssetRow | None = self._session.get(AssetRow, asset_id)
        if row is None:
            raise EntityNotFoundError("asset", str(asset_id))
        return self._wrap(row)

    def load_many(self, asset_ids: list[UUID]) -> list[Asset]:
        """Return assets in the requested order. Missing ids are skipped."""
        loaded: list[Asset] = []
        asset_id: UUID
        for asset_id in asset_ids:
            row: AssetRow | None = self._session.get(AssetRow, asset_id)
            if row is None:
                continue
            loaded.append(self._wrap(row))
        return loaded

    def _wrap(self, row: AssetRow) -> Asset:
        try:
            kind: AssetKind = AssetKind(row.kind)
        except ValueError as err:
            raise ValidationAppError(f"unknown asset kind: {row.kind}", field="kind") from err
        strategy: KindStrategy = _STRATEGIES[kind]
        return Asset(row, self._session, self._agents, strategy)
