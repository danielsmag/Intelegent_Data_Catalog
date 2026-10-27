"""Behavior that differs by asset kind."""

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import AssetAgent, AssetAgentProvider


class KindStrategy(Protocol):
    """Loads the source row, opens the agent, and builds the API detail."""

    def default_flags(self) -> dict[str, bool]:
        """Flags stored when the asset has no configuration row yet."""
        ...

    def agent(
        self,
        session: Session,
        agents: AssetAgentProvider,
        asset_id: UUID,
    ) -> AssetAgent:
        """Agent that builds this asset."""
        ...

    def detail(self, session: Session, asset_id: UUID) -> BaseModel:
        """Kind payload returned by the API."""
        ...
