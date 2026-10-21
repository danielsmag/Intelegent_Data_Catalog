"""Workspace file and notebook assets."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from intelegent_data_catalog.assets.base import Asset, AssetAgent
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.models import WorkspaceNodeRow

_FILE_OPTIONS: dict[str, bool] = {
    "include_outputs": False,
    "include_table_links": True,
}


class FileAssetDetail(BaseModel):
    """Workspace file behind a file or notebook asset."""

    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    path: str
    content_type: str | None
    size_bytes: int | None


class FileAsset(Asset):
    """A workspace file addressed as an asset."""

    def default_options(self) -> dict[str, bool]:
        """Flags for a file context build."""
        return dict(_FILE_OPTIONS)

    def agent(self) -> AssetAgent:
        """File agent. Notebooks pass ``notebook=True`` so outputs can be included."""
        return self._agents.for_file(self.id, self._notebook)

    def detail(self) -> FileAssetDetail:
        """File path and size."""
        node: WorkspaceNodeRow = self._node()
        return FileAssetDetail(
            node_id=node.id,
            path=node.path,
            content_type=node.content_type,
            size_bytes=node.size_bytes,
        )

    @property
    def _notebook(self) -> bool:
        return False

    def _node(self) -> WorkspaceNodeRow:
        statement = select(WorkspaceNodeRow).where(WorkspaceNodeRow.asset_id == self.id)
        node: WorkspaceNodeRow | None = self._session.scalar(statement)
        if node is None:
            raise EntityNotFoundError("workspace file", str(self.id))
        return node


class NotebookAsset(FileAsset):
    """A Python notebook. The file agent includes cell outputs when asked."""

    @property
    def _notebook(self) -> bool:
        return True
