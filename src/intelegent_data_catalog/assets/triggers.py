"""Start an asset rebuild when its source changes and the mode is ``ON_CHANGE``."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.models import AssetContextOptions
from intelegent_data_catalog.core.settings import (
    ModelId,
    ReasoningEffort,
    parse_effort,
    parse_model_id,
)
from intelegent_data_catalog.entity_configuration import option_values
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs
from intelegent_data_catalog.persistence.enums import AssetRefreshMode, RefreshSubjectKind
from intelegent_data_catalog.persistence.models import AssetRow, EntityConfigurationRow


class AssetChangeTrigger:
    """Enqueues one asset build when a saved configuration uses ``ON_CHANGE``."""

    def __init__(self, session: Session, jobs: CatalogJobs | None) -> None:
        self._session: Session = session
        self._jobs: CatalogJobs | None = jobs

    def changed(self, asset_id: UUID) -> None:
        """Queue a rebuild when the asset is configured to refresh on change."""
        if self._jobs is None:
            return
        asset: AssetRow | None = self._session.get(AssetRow, asset_id)
        if asset is None:
            return
        row: EntityConfigurationRow | None = self._session.scalar(
            select(EntityConfigurationRow).where(EntityConfigurationRow.asset_id == asset_id),
        )
        if row is None or not row.enabled:
            return
        if row.refresh_mode != AssetRefreshMode.ON_CHANGE.value:
            return
        flags: dict[str, bool] = option_values(
            RefreshSubjectKind.ASSET,
            dict(row.options),
            asset.kind,
        )
        model: ModelId = parse_model_id(row.model)
        effort: ReasoningEffort = parse_effort(row.effort)
        self._jobs.start_asset(
            asset.id,
            asset.name,
            AssetContextOptions(model=model, effort=effort, flags=flags),
        )
