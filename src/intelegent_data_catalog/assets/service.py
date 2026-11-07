"""Asset reads and writes for the API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import Asset, AssetAgent, AssetAgentProvider
from intelegent_data_catalog.assets.execution.models import AssetRunRead, AssetRunTaskDetail
from intelegent_data_catalog.assets.execution.runs import AssetRunReader
from intelegent_data_catalog.assets.models import (
    AssetContextOptions,
    AssetListItem,
    AssetPage,
    AssetQuery,
    AssetRead,
    AssetUpdate,
)
from intelegent_data_catalog.assets.registry import AssetRegistry
from intelegent_data_catalog.assets.repository import AssetRepository
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    LlmUnavailableError,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AssetKind, AssetStatus, ContextOrigin
from intelegent_data_catalog.persistence.models import (
    AssetRow,
    CatalogTableRow,
)
from intelegent_data_catalog.registry.reads import dump_model


class AssetService:
    """Lists and attaches assets. Operators create them. The caller owns the session."""

    def __init__(
        self,
        session: Session,
        access: AccessContext,
        agents: AssetAgentProvider | None = None,
    ) -> None:
        self._session: Session = session
        self._access: AccessContext = access
        self._repository: AssetRepository = AssetRepository(session)
        self._registry: AssetRegistry = AssetRegistry(session, agents or _UnavailableAgents())
        self._runs: AssetRunReader = AssetRunReader(session)

    def search(self, query: AssetQuery) -> AssetPage:
        """Return one page of assets the caller can see."""
        self._require_workspace(query.workspace_id)
        items, total = self._repository.search(query, self._access)
        visible: list[AssetListItem] = items
        return AssetPage(items=visible, total=total)

    def get(self, asset_id: UUID) -> AssetRead:
        """Return one visible asset."""
        row: AssetRow = self._require(asset_id)
        return self._read(row)

    def list_runs(self, asset_id: UUID, limit: int) -> list[AssetRunRead]:
        """Return the newest operator runs for one visible asset."""
        self._require(asset_id)
        bounded: int = min(max(limit, 1), 25)
        return self._runs.list_for_asset(asset_id, bounded)

    def get_run_task(self, asset_id: UUID, run_id: UUID, task_id: str) -> AssetRunTaskDetail:
        """Return one task log for a visible asset."""
        self._require(asset_id)
        return self._runs.get_task(asset_id, run_id, task_id)

    def update(self, asset_id: UUID, patch: AssetUpdate) -> AssetRead:
        """Patch name, description, context, or status."""
        row: AssetRow = self._require(asset_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes and patch.name is not None:
            row.name = patch.name
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            text: str | None = None if patch.context is None else patch.context.strip()
            if text == "":
                text = None
            row.context = text
            row.context_origin = None if text is None else ContextOrigin.USER.value
        if "status" in changes and patch.status is not None:
            row.status = patch.status.value
            self._mirror_table_status(row, patch.status)
        row.updated_at = utc_now()
        self._session.commit()
        return self._read(row)

    def _mirror_table_status(self, row: AssetRow, status: AssetStatus) -> None:
        """Keep a table asset and its catalog row on the same on/off state."""
        if row.kind != AssetKind.TABLE.value:
            return
        table: CatalogTableRow | None = self._session.scalar(
            select(CatalogTableRow).where(CatalogTableRow.asset_id == row.id),
        )
        if table is None:
            return
        table.status = status.value
        table.updated_at = utc_now()

    def delete(self, asset_id: UUID) -> None:
        """Delete the asset. The source table, DAG, or file stays."""
        row: AssetRow = self._require(asset_id)
        self._repository.delete(row)
        self._session.commit()






    def build(self, asset_id: UUID, options: AssetContextOptions) -> Asset:
        """Run the asset agent and store the snapshot. Used by the job worker."""
        row: AssetRow = self._require(asset_id)
        asset: Asset = self._registry.load(row.id)
        asset.build_context(options)
        return asset


    def _read(self, row: AssetRow) -> AssetRead:
        asset: Asset = self._registry.load(row.id)
        detail: BaseModel = asset.detail()
        dumped: object = dump_model(detail)
        payload: dict[str, JsonValue] = dumped if isinstance(dumped, dict) else {}
        origin: ContextOrigin | None = None
        if row.context_origin is not None:
            origin = ContextOrigin(row.context_origin)
        return AssetRead(
            id=row.id,
            kind=asset.kind,
            name=row.name,
            slug=row.slug,
            description=row.description,
            context=row.context,
            context_origin=origin,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            status=asset.status,
            properties=_properties(row.properties),
            tags=_tags(row.properties),
            detail=payload,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
            last_synced_at=None if row.last_synced_at is None else _aware(row.last_synced_at),
        )

    def _require(self, asset_id: UUID) -> AssetRow:
        row: AssetRow | None = self._repository.get(asset_id)
        if row is None or not self._can_see(row):
            raise EntityNotFoundError("asset", str(asset_id))
        return row

    def _can_see(self, row: AssetRow) -> bool:
        personal: bool = row.owner_user_id is not None and row.owner_user_id != self._access.user_id
        if personal and not self._access.principal.is_admin:
            return False
        return self._access.is_member(row.workspace_id)

    def _require_workspace(self, workspace_id: UUID | None) -> None:
        if workspace_id is None:
            return
        if not self._access.is_member(workspace_id):
            raise EntityNotFoundError("workspace", str(workspace_id))



class _UnavailableAgents(AssetAgentProvider):
    """Placeholder used when a request only reads assets."""

    def for_table(self, table_id: UUID, asset_id: UUID, *, primary: bool) -> AssetAgent:
        del table_id, asset_id, primary
        raise LlmUnavailableError("language model is not configured")

    def for_dag(self, dag_row_id: UUID, instance_id: UUID) -> AssetAgent:
        raise LlmUnavailableError("language model is not configured")

    def for_instance(self, instance_id: UUID) -> AssetAgent:
        raise LlmUnavailableError("language model is not configured")

    def for_file(self, asset_id: UUID, notebook: bool) -> AssetAgent:
        raise LlmUnavailableError("language model is not configured")


def _tags(raw: dict[str, object]) -> list[str]:
    stored: object = raw.get("tags")
    if not isinstance(stored, list):
        return []
    names: list[str] = []
    item: object
    for item in stored:
        if isinstance(item, str) and item != "":
            names.append(item)
    return names


def _properties(raw: dict[str, object]) -> dict[str, JsonValue]:
    stored: dict[str, JsonValue] = {}
    key: str
    value: object
    for key, value in raw.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            stored[key] = value
        elif isinstance(value, list):
            stored[key] = [
                item if isinstance(item, (str, int, float, bool)) or item is None else str(item)
                for item in value
            ]
        else:
            stored[key] = str(value)
    return stored


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        from datetime import UTC

        return value.replace(tzinfo=UTC)
    return value
