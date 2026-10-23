"""Keep an asset row next to its catalog table, DAG, or workspace file."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, object_session

from intelegent_data_catalog.assets.naming import AssetSlugs
from intelegent_data_catalog.assets.repository import AssetRepository
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.enums import AssetKind, AssetStatus
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AssetRow,
    CatalogTableRow,
    WorkspaceNodeRow,
)


class AssetSync:
    """Creates and updates the asset for a source row. The caller commits."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session
        self._repository: AssetRepository = AssetRepository(session)
        self._slugs: AssetSlugs = AssetSlugs(session)

    @staticmethod
    def for_row(row: object) -> AssetSync | None:
        """Build a sync from the row's session. None when the row is detached."""
        session: Session | None = object_session(row)
        if session is None:
            return None
        return AssetSync(session)

    def ensure_table(self, row: CatalogTableRow) -> AssetRow:
        """Create or refresh the table asset. Membership is not added here."""
        asset: AssetRow | None = self._existing(row.asset_id)
        now: datetime = utc_now()
        known: set[str] = {item.value for item in AssetStatus}
        status: str = row.status if row.status in known else AssetStatus.ACTIVE.value
        properties: dict[str, object] = {
            "source_key": row.source_key,
            "table_format": row.table_format,
            "table_id": str(row.id),
        }
        if asset is None:
            asset = self._insert(
                kind=AssetKind.TABLE,
                name=row.name,
                raw_slug=row.source_key or row.name,
                description=row.description,
                context=row.context,
                context_origin=row.context_origin,
                workspace_id=SHARED_WORKSPACE_ID,
                owner_user_id=None,
                created_by=None,
                properties=properties,
                status=status,
                last_synced_at=row.last_synced_at,
                now=now,
            )
            row.asset_id = asset.id
        else:
            asset.name = _clip(row.name)
            asset.description = row.description
            asset.status = status
            asset.properties = properties
            asset.last_synced_at = row.last_synced_at
            asset.updated_at = now
        self._session.flush()
        return asset

    def delete_table(self, row: CatalogTableRow) -> None:
        """Delete the asset linked to a catalog table."""
        self._delete_linked(row.asset_id)

    def ensure_dag(self, row: AirflowDagRow) -> AssetRow:
        """Create or refresh the DAG asset. Stale DAGs are marked missing."""
        asset: AssetRow | None = self._existing(row.asset_id)
        now: datetime = row.last_seen_at
        label: str = row.dag_id if row.display_name is None else row.display_name
        status: str = AssetStatus.MISSING.value if row.is_stale else AssetStatus.ACTIVE.value
        properties: dict[str, object] = {
            "dag_id": row.dag_id,
            "instance_id": str(row.instance_id),
        }
        if asset is None:
            asset = self._insert(
                kind=AssetKind.AIRFLOW_DAG,
                name=label,
                raw_slug=f"{row.instance_id}-{row.dag_id}",
                description=row.description,
                context=None,
                context_origin=None,
                workspace_id=SHARED_WORKSPACE_ID,
                owner_user_id=None,
                created_by=None,
                properties=properties,
                status=status,
                last_synced_at=row.last_seen_at,
                now=now,
            )
            row.asset_id = asset.id
        else:
            asset.name = _clip(label)
            asset.description = row.description
            asset.status = status
            asset.properties = properties
            asset.last_synced_at = row.last_seen_at
            asset.updated_at = utc_now()
        self._session.flush()
        return asset

    def ensure_file(self, row: WorkspaceNodeRow) -> AssetRow | None:
        """Create or refresh the file or notebook asset. Folders are ignored."""
        if row.kind != "FILE":
            return None
        asset: AssetRow | None = self._existing(row.asset_id)
        now: datetime = utc_now()
        kind: AssetKind = (
            AssetKind.NOTEBOOK if row.name.lower().endswith(".ipynb") else AssetKind.FILE
        )
        properties: dict[str, object] = {
            "content_type": row.content_type,
            "path": row.path,
            "node_id": str(row.id),
        }
        if asset is None:
            asset = self._insert(
                kind=kind,
                name=row.name,
                raw_slug=row.path,
                description=None,
                context=None,
                context_origin=None,
                workspace_id=row.workspace_id,
                owner_user_id=row.owner_user_id,
                created_by=row.created_by,
                properties=properties,
                status=AssetStatus.ACTIVE.value,
                last_synced_at=None,
                now=now,
            )
            row.asset_id = asset.id
        else:
            asset.kind = kind.value
            asset.name = _clip(row.name)
            asset.workspace_id = row.workspace_id
            asset.owner_user_id = row.owner_user_id
            asset.properties = properties
            asset.updated_at = now
        self._session.flush()
        return asset

    def delete_file(self, row: WorkspaceNodeRow) -> None:
        """Delete the asset linked to a workspace node."""
        self._delete_linked(row.asset_id)

    def _insert(
        self,
        *,
        kind: AssetKind,
        name: str,
        raw_slug: str,
        description: str | None,
        context: str | None,
        context_origin: str | None,
        workspace_id: UUID,
        owner_user_id: UUID | None,
        created_by: UUID | None,
        properties: dict[str, object],
        status: str,
        last_synced_at: datetime | None,
        now: datetime,
    ) -> AssetRow:
        slug: str = self._slugs.claim(workspace_id, owner_user_id, raw_slug)
        created: AssetRow = AssetRow(
            id=uuid4(),
            kind=kind.value,
            name=_clip(name),
            slug=slug,
            description=description,
            context=context,
            context_origin=context_origin,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            created_by=created_by,
            properties=properties,
            status=status,
            last_synced_at=last_synced_at,
            created_at=now,
            updated_at=now,
        )
        return self._repository.add(created)

    def _existing(self, asset_id: UUID | None) -> AssetRow | None:
        if asset_id is None:
            return None
        return self._repository.get(asset_id)

    def _delete_linked(self, asset_id: UUID | None) -> None:
        if asset_id is None:
            return
        asset: AssetRow | None = self._repository.get(asset_id)
        if asset is None:
            return
        self._repository.delete(asset)


def _clip(value: str) -> str:
    if len(value) <= 255:
        return value
    return value[:255]
