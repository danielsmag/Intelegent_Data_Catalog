"""Latest analysis snapshot for one agent."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.models import AgentAnalysisContext
from intelegent_data_catalog.exceptions import ConflictError, PersistenceUnavailableError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import AgentKnowledgeContextRow
from intelegent_data_catalog.registry.reads import dump_model

_CONFLICT: str = "agent knowledge context conflicts with an existing record"
_INVALID: str = "stored agent knowledge context is invalid"


class AgentKnowledgeStore:
    """One snapshot per agent. A later build replaces the payload."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def save(self, context: AgentAnalysisContext) -> None:
        """Insert or replace the snapshot for ``context.agent_id``."""
        payload: dict[str, object] = dump_model(context)
        now: datetime = utc_now()
        row: AgentKnowledgeContextRow | None = self._by_agent(context.agent_id)
        if row is None:
            created: AgentKnowledgeContextRow = AgentKnowledgeContextRow(
                id=uuid4(),
                agent_id=context.agent_id,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
        else:
            row.payload = payload
            row.updated_at = now
        self._commit()

    def get(self, agent_id: UUID) -> AgentAnalysisContext | None:
        """Return the snapshot, or none when this agent has never been built."""
        row: AgentKnowledgeContextRow | None = self._by_agent(agent_id)
        if row is None:
            return None
        return self._decode(row.payload, agent_id)

    def _by_agent(self, agent_id: UUID) -> AgentKnowledgeContextRow | None:
        statement = select(AgentKnowledgeContextRow).where(
            AgentKnowledgeContextRow.agent_id == agent_id,
        )
        return self._session.scalar(statement)

    def _decode(self, payload: object, agent_id: UUID) -> AgentAnalysisContext:
        if not isinstance(payload, dict):
            raise PersistenceUnavailableError(_INVALID)
        try:
            context: AgentAnalysisContext = AgentAnalysisContext.model_validate(payload)
        except ValidationError as err:
            raise PersistenceUnavailableError(_INVALID) from err
        if context.agent_id != agent_id:
            raise PersistenceUnavailableError(_INVALID)
        return context

    def _commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as err:
            self._session.rollback()
            raise ConflictError(_CONFLICT) from err
