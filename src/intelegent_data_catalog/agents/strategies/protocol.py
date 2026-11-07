"""Behavior that differs by agent kind."""

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.base import ContextBuilder, ContextBuilderProvider


class KindStrategy(Protocol):
    """Loads the source row, opens the agent, and builds the API detail."""

    def default_flags(self) -> dict[str, bool]:
        """Flags stored when the agent has no configuration row yet."""
        ...

    def builder(
        self,
        session: Session,
        agents: ContextBuilderProvider,
        agent_id: UUID,
    ) -> ContextBuilder:
        """Agent that builds this agent."""
        ...

    def detail(self, session: Session, agent_id: UUID) -> BaseModel:
        """Kind payload returned by the API."""
        ...
