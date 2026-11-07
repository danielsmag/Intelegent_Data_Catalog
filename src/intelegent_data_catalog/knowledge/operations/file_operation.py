"""Agent for one workspace file or notebook."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.base import ContextBuilder
from intelegent_data_catalog.agents.extract import FileText, extract_document
from intelegent_data_catalog.agents.models import (
    AgentAnalysisContext,
    AgentContextOptions,
    FileCatalogContext,
    FileContextProposal,
)
from intelegent_data_catalog.exceptions import LlmUnavailableError, ValidationAppError
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AgentKind
from intelegent_data_catalog.persistence.models import (
    AgentRow,
    CatalogTableRow,
    WorkspaceNodeRow,
)
from intelegent_data_catalog.storage.object_store import ObjectStore


class FileMetadata(BaseModel):
    """File identity without its bytes."""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    name: str
    path: str
    content_type: str | None


class FileAgent(ContextBuilder):
    """Read a workspace file and ask for a summary and business context."""

    def __init__(
        self,
        agent: AgentRow,
        session: Session,
        objects: ObjectStore | None,
        describer: ProposalAgent[FileCatalogContext, FileContextProposal] | None = None,
    ) -> None:
        self._agent: AgentRow = agent
        self._session: Session = session
        self._objects: ObjectStore | None = objects
        self._describer: ProposalAgent[FileCatalogContext, FileContextProposal] | None = describer

    def get_metadata(self) -> FileMetadata:
        """Return the file name and path."""
        node: WorkspaceNodeRow = self._node()
        return FileMetadata(
            agent_id=self._agent.id,
            name=node.name,
            path=node.path,
            content_type=node.content_type,
        )

    def build_context(self, options: AgentContextOptions) -> AgentAnalysisContext:
        """Extract text, link named tables, and optionally ask the model for a summary."""
        loaded: FileCatalogContext = self._context(options.flags.get("include_outputs", False))
        linked: list[str] = []
        if options.flags.get("include_table_links", True):
            linked = self._linked_tables(loaded.referenced_tables)
        summary: str | None = _lead(loaded.text)
        if self._describer is not None and loaded.text.strip() != "":
            proposed: FileContextProposal = self._describer.propose(loaded)
            if proposed.summary.strip() != "":
                summary = proposed.summary.strip()
        return AgentAnalysisContext(
            agent_id=self._agent.id,
            kind=AgentKind(self._agent.kind),
            summary=summary,
            built_at=utc_now(),
            payload={
                "path": loaded.name,
                "referenced_tables": linked,
                "text_chars": len(loaded.text),
            },
        )

    def describe(self) -> str:
        """Ask for business context and return the text. The agent stores it."""
        if self._describer is None:
            raise LlmUnavailableError("context generation is not configured")
        loaded: FileCatalogContext = self._context(include_outputs=True)
        proposed: FileContextProposal = self._describer.propose(loaded)
        cleaned: str = proposed.context.strip()
        if cleaned == "":
            raise LlmUnavailableError("context generation returned an empty answer")
        return cleaned

    def _context(self, include_outputs: bool) -> FileCatalogContext:
        node: WorkspaceNodeRow = self._node()
        payload: bytes = b""
        if node.object_key is not None and self._objects is not None:
            payload = self._objects.get(node.object_key)
        extracted: FileText = extract_document(
            node.name,
            node.content_type,
            payload,
            include_outputs=include_outputs,
        )
        return FileCatalogContext(
            agent_id=self._agent.id,
            name=node.name,
            kind=AgentKind(self._agent.kind),
            content_type=node.content_type,
            text=extracted.text,
            referenced_tables=extracted.referenced_tables,
        )

    def _node(self) -> WorkspaceNodeRow:
        statement = select(WorkspaceNodeRow).where(WorkspaceNodeRow.agent_id == self._agent.id)
        node: WorkspaceNodeRow | None = self._session.scalar(statement)
        if node is None:
            raise ValidationAppError("agent has no workspace file", field="agent_id")
        return node

    def _linked_tables(self, names: list[str]) -> list[str]:
        if len(names) == 0:
            return []
        wanted: set[str] = {name.casefold() for name in names}
        statement = select(CatalogTableRow.name)
        linked: list[str] = []
        table_name: str
        for table_name in self._session.scalars(statement):
            if table_name.casefold() in wanted and table_name not in linked:
                linked.append(table_name)
        return linked


def _lead(text: str) -> str | None:
    cleaned: str = " ".join(text.split())
    if cleaned == "":
        return None
    if len(cleaned) <= 240:
        return cleaned
    return f"{cleaned[:240]}..."
