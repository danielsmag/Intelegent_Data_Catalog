"""Asset reads and writes for the API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import Asset, AssetAgent, AssetAgentProvider
from intelegent_data_catalog.assets.models import (
    AssetContextOptions,
    AssetCreate,
    AssetListItem,
    AssetMembershipRead,
    AssetPage,
    AssetQuery,
    AssetRead,
    AssetUpdate,
    BulkMembershipResult,
    BulkMembershipWrite,
)
from intelegent_data_catalog.assets.registry import AssetRegistry
from intelegent_data_catalog.assets.repository import AssetRepository
from intelegent_data_catalog.assets.sync import AssetSync
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    LlmUnavailableError,
    ValidationAppError,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import ContextOrigin
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AssetMembershipRow,
    AssetRow,
    CatalogTableRow,
    ScopeRow,
    SpaceRow,
    WorkspaceNodeRow,
)
from intelegent_data_catalog.registry.reads import dump_model


class AssetService:
    """Creates, lists, and attaches assets. The caller owns the session."""

    def __init__(
        self,
        session: Session,
        access: AccessContext,
        agents: AssetAgentProvider | None = None,
    ) -> None:
        self._session: Session = session
        self._access: AccessContext = access
        self._repository: AssetRepository = AssetRepository(session)
        self._sync: AssetSync = AssetSync(session)
        self._registry: AssetRegistry = AssetRegistry(session, agents or _UnavailableAgents())

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

    def create(self, body: AssetCreate) -> AssetRead:
        """Link an existing table, DAG, or file and optionally attach it."""
        self._require_workspace(body.workspace_id)
        sources: int = len([item for item in (body.table, body.dag, body.node) if item is not None])
        if sources != 1:
            raise ValidationAppError("provide one of table, dag, or node", field="source")
        row: AssetRow
        if body.table is not None:
            table: CatalogTableRow | None = self._session.get(CatalogTableRow, body.table.table_id)
            if table is None:
                raise EntityNotFoundError("catalog table", str(body.table.table_id))
            scope_id: UUID | None = body.table.scope_id or body.scope_id or table.scope_id
            row = self._sync.ensure_table(table, scope_id)
        elif body.dag is not None:
            dag: AirflowDagRow | None = self._session.scalar(
                _dag_statement(body.dag.instance_id, body.dag.dag_id),
            )
            if dag is None:
                raise EntityNotFoundError("airflow dag", body.dag.dag_id)
            row = self._sync.ensure_dag(dag)
        else:
            node_id: UUID = body.node.node_id if body.node is not None else body.workspace_id
            node: WorkspaceNodeRow | None = self._session.get(WorkspaceNodeRow, node_id)
            if node is None or node.workspace_id != body.workspace_id:
                raise EntityNotFoundError("workspace file", str(node_id))
            self._require_node(node)
            created: AssetRow | None = self._sync.ensure_file(node)
            if created is None:
                raise ValidationAppError("folders are not assets", field="node")
            row = created
        if body.scope_id is not None:
            self._require_scope(body.scope_id)
            self._repository.attach(row.id, scope_id=body.scope_id, space_id=None)
        if body.space_id is not None:
            self._require_space(body.space_id)
            self._repository.attach(row.id, scope_id=None, space_id=body.space_id)
        self._session.commit()
        return self._read(row)

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
        row.updated_at = utc_now()
        self._session.commit()
        return self._read(row)

    def delete(self, asset_id: UUID) -> None:
        """Delete the asset. The source table, DAG, or file stays."""
        row: AssetRow = self._require(asset_id)
        self._repository.delete(row)
        self._session.commit()

    def list_scope(self, scope_id: UUID) -> list[AssetRead]:
        """Assets attached to one scope."""
        self._require_scope(scope_id)
        visible: list[AssetRead] = []
        row: AssetRow
        for row in self._repository.list_for_scope(scope_id):
            if self._can_see(row):
                visible.append(self._read(row))
        return visible

    def list_space(self, space_id: UUID) -> list[AssetRead]:
        """Assets attached to one space or its scopes."""
        self._require_space(space_id)
        visible: list[AssetRead] = []
        row: AssetRow
        for row in self._repository.list_for_space(space_id):
            if self._can_see(row):
                visible.append(self._read(row))
        return visible

    def attach_many(
        self,
        target_kind: str,
        target_id: UUID,
        asset_ids: list[UUID],
    ) -> BulkMembershipResult:
        """Attach assets to a scope or space."""
        return self._bulk(
            BulkMembershipWrite(
                asset_ids=asset_ids,
                target_kind="scope" if target_kind == "scope" else "space",
                target_id=target_id,
                action="attach",
            ),
        )

    def detach_one(self, target_kind: str, target_id: UUID, asset_id: UUID) -> None:
        """Remove one membership."""
        self._bulk(
            BulkMembershipWrite(
                asset_ids=[asset_id],
                target_kind="scope" if target_kind == "scope" else "space",
                target_id=target_id,
                action="detach",
            ),
        )

    def bulk(self, body: BulkMembershipWrite) -> BulkMembershipResult:
        """Attach or detach many assets."""
        return self._bulk(body)

    def build(self, asset_id: UUID, options: AssetContextOptions) -> Asset:
        """Run the asset agent and store the snapshot. Used by the job worker."""
        row: AssetRow = self._require(asset_id)
        asset: Asset = self._registry.load(row.id)
        asset.build_context(options)
        return asset

    def _bulk(self, body: BulkMembershipWrite) -> BulkMembershipResult:
        if body.target_kind == "scope":
            self._require_scope(body.target_id)
            scope_id: UUID | None = body.target_id
            space_id: UUID | None = None
        else:
            self._require_space(body.target_id)
            scope_id = None
            space_id = body.target_id
        changed: int = 0
        unchanged: int = 0
        asset_id: UUID
        for asset_id in body.asset_ids:
            row: AssetRow = self._require(asset_id)
            if body.action == "attach":
                _membership, created = self._repository.attach(
                    row.id,
                    scope_id=scope_id,
                    space_id=space_id,
                )
                if created:
                    changed += 1
                else:
                    unchanged += 1
            else:
                existing: AssetMembershipRow | None = self._repository.membership_for(
                    row.id,
                    scope_id=scope_id,
                    space_id=space_id,
                )
                if existing is None:
                    unchanged += 1
                else:
                    self._repository.detach(existing)
                    changed += 1
        self._session.commit()
        return BulkMembershipResult(changed=changed, unchanged=unchanged)

    def _read(self, row: AssetRow) -> AssetRead:
        asset: Asset = self._registry.load(row.id)
        detail: BaseModel = asset.detail()
        dumped: object = dump_model(detail)
        payload: dict[str, JsonValue] = dumped if isinstance(dumped, dict) else {}
        memberships: list[AssetMembershipRead] = asset.memberships()
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
            memberships=memberships,
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

    def _require_scope(self, scope_id: UUID) -> ScopeRow:
        row: ScopeRow | None = self._session.get(ScopeRow, scope_id)
        if row is None or not self._access.allows_space(row.space_id):
            raise EntityNotFoundError("scope", str(scope_id))
        return row

    def _require_space(self, space_id: UUID) -> SpaceRow:
        row: SpaceRow | None = self._session.get(SpaceRow, space_id)
        if row is None or not self._access.allows_space(space_id):
            raise EntityNotFoundError("space", str(space_id))
        return row

    def _require_node(self, node: WorkspaceNodeRow) -> None:
        if not self._access.is_member(node.workspace_id):
            raise EntityNotFoundError("workspace file", str(node.id))
        hidden: bool = node.owner_user_id is not None and node.owner_user_id != self._access.user_id
        if hidden and not self._access.principal.is_admin:
            raise EntityNotFoundError("workspace file", str(node.id))


class _UnavailableAgents(AssetAgentProvider):
    """Placeholder used when a request only reads assets."""

    def for_table(self, table_id: UUID) -> AssetAgent:
        raise LlmUnavailableError("language model is not configured")

    def for_dag(self, dag_row_id: UUID, instance_id: UUID) -> AssetAgent:
        raise LlmUnavailableError("language model is not configured")

    def for_file(self, asset_id: UUID, notebook: bool) -> AssetAgent:
        raise LlmUnavailableError("language model is not configured")


def _dag_statement(instance_id: UUID, dag_id: str) -> Select[tuple[AirflowDagRow]]:
    statement: Select[tuple[AirflowDagRow]] = select(AirflowDagRow).where(
        AirflowDagRow.instance_id == instance_id,
        AirflowDagRow.dag_id == dag_id,
    )
    return statement


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
