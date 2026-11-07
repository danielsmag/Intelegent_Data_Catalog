"""Load one agent and the strategy for its stored kind."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.base import Agent, ContextBuilderProvider
from intelegent_data_catalog.agents.strategies.dag import DagStrategy
from intelegent_data_catalog.agents.strategies.file import FileStrategy
from intelegent_data_catalog.agents.strategies.instance import InstanceStrategy
from intelegent_data_catalog.agents.strategies.protocol import KindStrategy
from intelegent_data_catalog.agents.strategies.schema import SchemaStrategy
from intelegent_data_catalog.agents.strategies.table import TableStrategy
from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.persistence.enums import AgentKind
from intelegent_data_catalog.persistence.models import AgentRow

_STRATEGIES: dict[AgentKind, KindStrategy] = {
    AgentKind.TABLE: TableStrategy(),
    AgentKind.AIRFLOW_DAG: DagStrategy(),
    AgentKind.AIRFLOW_INSTANCE: InstanceStrategy(),
    AgentKind.NOTEBOOK: FileStrategy(notebook=True),
    AgentKind.FILE: FileStrategy(notebook=False),
    AgentKind.SCHEMA: SchemaStrategy(),
}


class AgentRegistry:
    """Maps ``AgentKind`` to the strategy that owns that kind's agent."""

    def __init__(self, session: Session, agents: ContextBuilderProvider) -> None:
        self._session: Session = session
        self._agents: ContextBuilderProvider = agents

    def load(self, agent_id: UUID) -> Agent:
        """Return the agent. Missing ids raise."""
        row: AgentRow | None = self._session.get(AgentRow, agent_id)
        if row is None:
            raise EntityNotFoundError("agent", str(agent_id))
        return self._wrap(row)

    def load_many(self, agent_ids: list[UUID]) -> list[Agent]:
        """Return agents in the requested order. Missing ids are skipped."""
        loaded: list[Agent] = []
        agent_id: UUID
        for agent_id in agent_ids:
            row: AgentRow | None = self._session.get(AgentRow, agent_id)
            if row is None:
                continue
            loaded.append(self._wrap(row))
        return loaded

    def _wrap(self, row: AgentRow) -> Agent:
        try:
            kind: AgentKind = AgentKind(row.kind)
        except ValueError as err:
            raise ValidationAppError(f"unknown agent kind: {row.kind}", field="kind") from err
        strategy: KindStrategy = _STRATEGIES[kind]
        return Agent(row, self._session, self._agents, strategy)
