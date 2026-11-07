"""Workspace file and notebook kind."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.base import ContextBuilder, ContextBuilderProvider
from intelegent_data_catalog.agents.strategies.flags import FILE_FLAGS
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.models import WorkspaceNodeRow


class FileAgentDetail(BaseModel):
    """Workspace file behind a file or notebook agent."""

    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    path: str
    content_type: str | None
    size_bytes: int | None


class FileStrategy:
    """A workspace file or notebook addressed through one agent id."""

    def __init__(self, notebook: bool) -> None:
        self._notebook: bool = notebook

    def default_flags(self) -> dict[str, bool]:
        """Flags for a file or notebook context build."""
        return dict(FILE_FLAGS)

    def builder(
        self,
        session: Session,
        agents: ContextBuilderProvider,
        agent_id: UUID,
    ) -> ContextBuilder:
        """File agent. Notebooks pass ``notebook=True`` so outputs can be included."""
        del session
        return agents.for_file(agent_id, self._notebook)

    def detail(self, session: Session, agent_id: UUID) -> FileAgentDetail:
        """File path and size."""
        node: WorkspaceNodeRow = _node(session, agent_id)
        return FileAgentDetail(
            node_id=node.id,
            path=node.path,
            content_type=node.content_type,
            size_bytes=node.size_bytes,
        )


def _node(session: Session, agent_id: UUID) -> WorkspaceNodeRow:
    node: WorkspaceNodeRow | None = session.scalar(
        select(WorkspaceNodeRow).where(WorkspaceNodeRow.agent_id == agent_id),
    )
    if node is None:
        raise EntityNotFoundError("workspace file", str(agent_id))
    return node
