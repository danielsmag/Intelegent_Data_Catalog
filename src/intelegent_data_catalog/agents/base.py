"""Agent base class. A kind strategy supplies the agent and detail payload."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.models import (
    AgentAnalysisContext,
    AgentContextOptions,
)
from intelegent_data_catalog.agents.repository import AgentRepository
from intelegent_data_catalog.agents.store import AgentKnowledgeStore
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AgentKind, AgentStatus, ContextOrigin
from intelegent_data_catalog.persistence.models import AgentRow

if TYPE_CHECKING:
    from intelegent_data_catalog.agents.strategies.protocol import KindStrategy


class ContextBuilder(ABC):
    """The worker that reads one agent and writes its context."""

    @abstractmethod
    def get_metadata(self) -> BaseModel:
        """Return the kind-specific facts the agent can see."""

    @abstractmethod
    def build_context(self, options: AgentContextOptions) -> AgentAnalysisContext:
        """Build and return an analysis snapshot. The agent stores it."""

    @abstractmethod
    def describe(self) -> str:
        """Return business context. The agent stores it as ``LLM``."""


class ContextBuilderProvider(ABC):
    """Opens the agent for one agent kind. ``RequestPlatform`` is the production provider."""

    @abstractmethod
    def for_table(self, table_id: UUID, agent_id: UUID, *, primary: bool) -> ContextBuilder:
        """Agent for one catalog table.

        ``primary`` is false for an alias agent. Its describe must not write the shared table row.
        """

    @abstractmethod
    def for_dag(self, dag_row_id: UUID, instance_id: UUID) -> ContextBuilder:
        """Agent for one Airflow DAG."""

    @abstractmethod
    def for_instance(self, instance_id: UUID) -> ContextBuilder:
        """Agent for one Airflow instance."""

    @abstractmethod
    def for_file(self, agent_id: UUID, notebook: bool) -> ContextBuilder:
        """Agent for one workspace file or notebook."""

    @abstractmethod
    def for_schema(self, agent_id: UUID) -> ContextBuilder:
        """Agent for one schema. One context covers every matching table."""


class Agent:
    """One catalog object. The strategy loads the source row and opens the agent."""

    def __init__(
        self,
        row: AgentRow,
        session: Session,
        agents: ContextBuilderProvider,
        strategy: KindStrategy,
    ) -> None:
        self._row: AgentRow = row
        self._session: Session = session
        self._agents: ContextBuilderProvider = agents
        self._strategy: KindStrategy = strategy
        self._repository: AgentRepository = AgentRepository(session)

    @property
    def id(self) -> UUID:
        """Agent id."""
        return self._row.id

    @property
    def kind(self) -> AgentKind:
        """Agent kind."""
        return AgentKind(self._row.kind)

    @property
    def name(self) -> str:
        """Display name."""
        return self._row.name

    @property
    def workspace_id(self) -> UUID:
        """Workspace that owns the agent."""
        return self._row.workspace_id

    @property
    def owner_user_id(self) -> UUID | None:
        """Personal owner. ``None`` means the agent is shared in the workspace."""
        return self._row.owner_user_id

    @property
    def context(self) -> str | None:
        """Business context text."""
        return self._row.context

    @property
    def properties(self) -> dict[str, object]:
        """Kind-specific properties."""
        return self._row.properties

    @property
    def status(self) -> AgentStatus:
        """Observation status."""
        return AgentStatus(self._row.status)

    @property
    def row(self) -> AgentRow:
        """Persistence row. Callers that sync source rows use this."""
        return self._row


    def saved_context(self) -> AgentAnalysisContext | None:
        """Return the last snapshot, or none."""
        return AgentKnowledgeStore(self._session).get(self.id)

    def build_context(self, options: AgentContextOptions) -> AgentAnalysisContext:
        """Ask the agent for a snapshot and store it."""
        built: AgentAnalysisContext = self.builder().build_context(options)
        AgentKnowledgeStore(self._session).save(built)
        return built

    def describe(self) -> str:
        """Ask the agent for business context and store it on the agent."""
        text: str = self.builder().describe()
        self._row.context = text
        self._row.context_origin = ContextOrigin.LLM.value
        self._row.updated_at = utc_now()
        self._session.commit()
        return text

    def builder(self) -> ContextBuilder:
        """Agent that builds this agent."""
        return self._strategy.builder(self._session, self._agents, self.id)

    def detail(self) -> BaseModel:
        """Kind payload returned by the API."""
        return self._strategy.detail(self._session, self.id)


