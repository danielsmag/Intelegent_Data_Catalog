"""Agent reads and writes for the API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.base import Agent, ContextBuilder, ContextBuilderProvider
from intelegent_data_catalog.agents.execution.models import AgentRunRead, AgentRunTaskDetail
from intelegent_data_catalog.agents.execution.runs import AgentRunReader
from intelegent_data_catalog.agents.models import (
    AgentContextOptions,
    AgentListItem,
    AgentPage,
    AgentQuery,
    AgentRead,
    AgentUpdate,
)
from intelegent_data_catalog.agents.registry import AgentRegistry
from intelegent_data_catalog.agents.repository import AgentRepository
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    LlmUnavailableError,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AgentKind, AgentStatus, ContextOrigin
from intelegent_data_catalog.persistence.models import (
    AgentRow,
    CatalogTableRow,
)
from intelegent_data_catalog.registry.reads import dump_model


class AgentService:
    """Lists and attaches agents. Operators create them. The caller owns the session."""

    def __init__(
        self,
        session: Session,
        access: AccessContext,
        agents: ContextBuilderProvider | None = None,
    ) -> None:
        self._session: Session = session
        self._access: AccessContext = access
        self._repository: AgentRepository = AgentRepository(session)
        self._registry: AgentRegistry = AgentRegistry(session, agents or _UnavailableAgents())
        self._runs: AgentRunReader = AgentRunReader(session)

    def search(self, query: AgentQuery) -> AgentPage:
        """Return one page of agents the caller can see."""
        self._require_workspace(query.workspace_id)
        items, total = self._repository.search(query, self._access)
        visible: list[AgentListItem] = items
        return AgentPage(items=visible, total=total)

    def get(self, agent_id: UUID) -> AgentRead:
        """Return one visible agent."""
        row: AgentRow = self._require(agent_id)
        return self._read(row)

    def list_runs(self, agent_id: UUID, limit: int) -> list[AgentRunRead]:
        """Return the newest operator runs for one visible agent."""
        self._require(agent_id)
        bounded: int = min(max(limit, 1), 25)
        return self._runs.list_for_agent(agent_id, bounded)

    def get_run_task(self, agent_id: UUID, run_id: UUID, task_id: str) -> AgentRunTaskDetail:
        """Return one task log for a visible agent."""
        self._require(agent_id)
        return self._runs.get_task(agent_id, run_id, task_id)

    def update(self, agent_id: UUID, patch: AgentUpdate) -> AgentRead:
        """Patch name, description, context, or status."""
        row: AgentRow = self._require(agent_id)
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

    def _mirror_table_status(self, row: AgentRow, status: AgentStatus) -> None:
        """Keep a table agent and its catalog row on the same on/off state."""
        if row.kind != AgentKind.TABLE.value:
            return
        table: CatalogTableRow | None = self._session.scalar(
            select(CatalogTableRow).where(CatalogTableRow.agent_id == row.id),
        )
        if table is None:
            return
        table.status = status.value
        table.updated_at = utc_now()

    def delete(self, agent_id: UUID) -> None:
        """Delete the agent. The source table, DAG, or file stays."""
        row: AgentRow = self._require(agent_id)
        self._repository.delete(row)
        self._session.commit()






    def build(self, agent_id: UUID, options: AgentContextOptions) -> Agent:
        """Run the agent agent and store the snapshot. Used by the job worker."""
        row: AgentRow = self._require(agent_id)
        agent: Agent = self._registry.load(row.id)
        agent.build_context(options)
        return agent


    def _read(self, row: AgentRow) -> AgentRead:
        agent: Agent = self._registry.load(row.id)
        detail: BaseModel = agent.detail()
        dumped: object = dump_model(detail)
        payload: dict[str, JsonValue] = dumped if isinstance(dumped, dict) else {}
        origin: ContextOrigin | None = None
        if row.context_origin is not None:
            origin = ContextOrigin(row.context_origin)
        return AgentRead(
            id=row.id,
            kind=agent.kind,
            name=row.name,
            slug=row.slug,
            description=row.description,
            context=row.context,
            context_origin=origin,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            status=agent.status,
            properties=_properties(row.properties),
            tags=_tags(row.properties),
            detail=payload,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
            last_synced_at=None if row.last_synced_at is None else _aware(row.last_synced_at),
        )

    def _require(self, agent_id: UUID) -> AgentRow:
        row: AgentRow | None = self._repository.get(agent_id)
        if row is None or not self._can_see(row):
            raise EntityNotFoundError("agent", str(agent_id))
        return row

    def _can_see(self, row: AgentRow) -> bool:
        personal: bool = row.owner_user_id is not None and row.owner_user_id != self._access.user_id
        if personal and not self._access.principal.is_admin:
            return False
        return self._access.is_member(row.workspace_id)

    def _require_workspace(self, workspace_id: UUID | None) -> None:
        if workspace_id is None:
            return
        if not self._access.is_member(workspace_id):
            raise EntityNotFoundError("workspace", str(workspace_id))



class _UnavailableAgents(ContextBuilderProvider):
    """Placeholder used when a request only reads agents."""

    def for_table(self, table_id: UUID, agent_id: UUID, *, primary: bool) -> ContextBuilder:
        del table_id, agent_id, primary
        raise LlmUnavailableError("language model is not configured")

    def for_dag(self, dag_row_id: UUID, instance_id: UUID) -> ContextBuilder:
        raise LlmUnavailableError("language model is not configured")

    def for_instance(self, instance_id: UUID) -> ContextBuilder:
        raise LlmUnavailableError("language model is not configured")

    def for_file(self, agent_id: UUID, notebook: bool) -> ContextBuilder:
        raise LlmUnavailableError("language model is not configured")

    def for_schema(self, agent_id: UUID) -> ContextBuilder:
        del agent_id
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
