"""Start an agent rebuild when its source changes and the mode is ``ON_CHANGE``."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.models import AgentContextOptions
from intelegent_data_catalog.core.settings import (
    ModelId,
    ReasoningEffort,
    parse_effort,
    parse_model_id,
)
from intelegent_data_catalog.entity_configuration import option_values
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs
from intelegent_data_catalog.persistence.enums import AgentRefreshMode, RefreshSubjectKind
from intelegent_data_catalog.persistence.models import AgentRow, EntityConfigurationRow


class AgentChangeTrigger:
    """Enqueues one agent build when a saved configuration uses ``ON_CHANGE``."""

    def __init__(self, session: Session, jobs: CatalogJobs | None) -> None:
        self._session: Session = session
        self._jobs: CatalogJobs | None = jobs

    def changed(self, agent_id: UUID) -> None:
        """Queue a rebuild when the agent is configured to refresh on change."""
        if self._jobs is None:
            return
        agent: AgentRow | None = self._session.get(AgentRow, agent_id)
        if agent is None:
            return
        row: EntityConfigurationRow | None = self._session.scalar(
            select(EntityConfigurationRow).where(EntityConfigurationRow.agent_id == agent_id),
        )
        if row is None or not row.enabled:
            return
        if row.refresh_mode != AgentRefreshMode.ON_CHANGE.value:
            return
        flags: dict[str, bool] = option_values(
            RefreshSubjectKind.AGENT,
            dict(row.options),
            agent.kind,
        )
        model: ModelId = parse_model_id(row.model)
        effort: ReasoningEffort = parse_effort(row.effort)
        self._jobs.start_agent(
            agent.id,
            agent.name,
            AgentContextOptions(model=model, effort=effort, flags=flags),
        )
