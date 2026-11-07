"""Asset base class. A kind strategy supplies the agent and detail payload."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.models import (
    AssetAnalysisContext,
    AssetContextOptions,
    AssetMembershipRead,
)
from intelegent_data_catalog.assets.repository import AssetRepository
from intelegent_data_catalog.assets.store import AssetKnowledgeStore
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AssetKind, AssetStatus, ContextOrigin
from intelegent_data_catalog.persistence.models import AssetMembershipRow, AssetRow

if TYPE_CHECKING:
    from intelegent_data_catalog.assets.strategies.protocol import KindStrategy


class AssetAgent(ABC):
    """The worker that reads one asset and writes its context."""

    @abstractmethod
    def get_metadata(self) -> BaseModel:
        """Return the kind-specific facts the agent can see."""

    @abstractmethod
    def build_context(self, options: AssetContextOptions) -> AssetAnalysisContext:
        """Build and return an analysis snapshot. The asset stores it."""

    @abstractmethod
    def describe(self) -> str:
        """Return business context. The asset stores it as ``LLM``."""


class AssetAgentProvider(ABC):
    """Opens the agent for one asset kind. ``RequestPlatform`` is the production provider."""

    @abstractmethod
    def for_table(self, table_id: UUID, asset_id: UUID, *, primary: bool) -> AssetAgent:
        """Agent for one catalog table.

        ``primary`` is false for an alias asset. Its describe must not write the shared table row.
        """

    @abstractmethod
    def for_dag(self, dag_row_id: UUID, instance_id: UUID) -> AssetAgent:
        """Agent for one Airflow DAG."""

    @abstractmethod
    def for_instance(self, instance_id: UUID) -> AssetAgent:
        """Agent for one Airflow instance."""

    @abstractmethod
    def for_file(self, asset_id: UUID, notebook: bool) -> AssetAgent:
        """Agent for one workspace file or notebook."""


class Asset:
    """One catalog object. The strategy loads the source row and opens the agent."""

    def __init__(
        self,
        row: AssetRow,
        session: Session,
        agents: AssetAgentProvider,
        strategy: KindStrategy,
    ) -> None:
        self._row: AssetRow = row
        self._session: Session = session
        self._agents: AssetAgentProvider = agents
        self._strategy: KindStrategy = strategy
        self._repository: AssetRepository = AssetRepository(session)

    @property
    def id(self) -> UUID:
        """Asset id."""
        return self._row.id

    @property
    def kind(self) -> AssetKind:
        """Asset kind."""
        return AssetKind(self._row.kind)

    @property
    def name(self) -> str:
        """Display name."""
        return self._row.name

    @property
    def workspace_id(self) -> UUID:
        """Workspace that owns the asset."""
        return self._row.workspace_id

    @property
    def owner_user_id(self) -> UUID | None:
        """Personal owner. ``None`` means the asset is shared in the workspace."""
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
    def status(self) -> AssetStatus:
        """Observation status."""
        return AssetStatus(self._row.status)

    @property
    def row(self) -> AssetRow:
        """Persistence row. Callers that sync source rows use this."""
        return self._row

    def memberships(self) -> list[AssetMembershipRead]:
        """Spaces and scopes that hold this asset."""
        rows: list[AssetMembershipRow] = self._repository.memberships(self.id)
        return [_membership(row) for row in rows]

    def saved_context(self) -> AssetAnalysisContext | None:
        """Return the last snapshot, or none."""
        return AssetKnowledgeStore(self._session).get(self.id)

    def build_context(self, options: AssetContextOptions) -> AssetAnalysisContext:
        """Ask the agent for a snapshot and store it."""
        built: AssetAnalysisContext = self.agent().build_context(options)
        AssetKnowledgeStore(self._session).save(built)
        return built

    def describe(self) -> str:
        """Ask the agent for business context and store it on the asset."""
        text: str = self.agent().describe()
        self._row.context = text
        self._row.context_origin = ContextOrigin.LLM.value
        self._row.updated_at = utc_now()
        self._session.commit()
        return text

    def agent(self) -> AssetAgent:
        """Agent that builds this asset."""
        return self._strategy.agent(self._session, self._agents, self.id)

    def detail(self) -> BaseModel:
        """Kind payload returned by the API."""
        return self._strategy.detail(self._session, self.id)


def _membership(row: AssetMembershipRow) -> AssetMembershipRead:
    return AssetMembershipRead(
        id=row.id,
        asset_id=row.asset_id,
        space_id=row.space_id,
        scope_id=row.scope_id,
        created_at=row.created_at,
    )
