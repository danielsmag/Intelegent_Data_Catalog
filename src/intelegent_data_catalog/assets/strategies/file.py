"""Workspace file and notebook kind."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import AssetAgent, AssetAgentProvider
from intelegent_data_catalog.assets.strategies.flags import FILE_FLAGS
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.models import WorkspaceNodeRow


class FileAssetDetail(BaseModel):
    """Workspace file behind a file or notebook asset."""

    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    path: str
    content_type: str | None
    size_bytes: int | None


class FileStrategy:
    """A workspace file or notebook addressed through one asset id."""

    def __init__(self, notebook: bool) -> None:
        self._notebook: bool = notebook

    def default_flags(self) -> dict[str, bool]:
        """Flags for a file or notebook context build."""
        return dict(FILE_FLAGS)

    def agent(
        self,
        session: Session,
        agents: AssetAgentProvider,
        asset_id: UUID,
    ) -> AssetAgent:
        """File agent. Notebooks pass ``notebook=True`` so outputs can be included."""
        del session
        return agents.for_file(asset_id, self._notebook)

    def detail(self, session: Session, asset_id: UUID) -> FileAssetDetail:
        """File path and size."""
        node: WorkspaceNodeRow = _node(session, asset_id)
        return FileAssetDetail(
            node_id=node.id,
            path=node.path,
            content_type=node.content_type,
            size_bytes=node.size_bytes,
        )


def _node(session: Session, asset_id: UUID) -> WorkspaceNodeRow:
    node: WorkspaceNodeRow | None = session.scalar(
        select(WorkspaceNodeRow).where(WorkspaceNodeRow.asset_id == asset_id),
    )
    if node is None:
        raise EntityNotFoundError("workspace file", str(asset_id))
    return node
