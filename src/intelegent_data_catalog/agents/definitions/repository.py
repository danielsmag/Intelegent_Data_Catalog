"""Reads and writes definition file rows. The caller commits."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.definitions.layout import KIND_RANK
from intelegent_data_catalog.persistence.enums import (
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
)
from intelegent_data_catalog.persistence.models import (
    AgentDefinitionFileRow,
    AgentDefinitionItemRow,
)


class AgentDefinitionRepository:
    """Definition file persistence."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def get_by_key(self, key: str) -> AgentDefinitionFileRow | None:
        """Return the file stored at ``key``."""
        return self._session.scalar(
            select(AgentDefinitionFileRow).where(AgentDefinitionFileRow.key == key),
        )

    def get(self, file_id: UUID) -> AgentDefinitionFileRow | None:
        """Return one file."""
        return self._session.get(AgentDefinitionFileRow, file_id)

    def add_file(self, row: AgentDefinitionFileRow) -> AgentDefinitionFileRow:
        """Insert a file. The caller commits."""
        self._session.add(row)
        self._session.flush()
        return row

    def list_files(self, folder: DefinitionKind | None) -> list[AgentDefinitionFileRow]:
        """Return files in key order. ``folder`` limits the kind."""
        statement = select(AgentDefinitionFileRow).order_by(AgentDefinitionFileRow.key)
        if folder is not None:
            statement = statement.where(AgentDefinitionFileRow.folder_kind == folder.value)
        return list(self._session.scalars(statement))

    def file_ids_by_key(self, keys: list[str]) -> dict[str, UUID]:
        """Return the scan row id for each file key that has been scanned."""
        found: dict[str, UUID] = {}
        if len(keys) == 0:
            return found
        statement = select(AgentDefinitionFileRow.key, AgentDefinitionFileRow.id).where(
            AgentDefinitionFileRow.key.in_(keys),
        )
        for record in self._session.execute(statement):
            key: str = record[0]
            file_id: UUID = record[1]
            found[key] = file_id
        return found

    def agent_ids_by_key(self, keys: list[str]) -> dict[str, list[UUID]]:
        """Return catalog agent ids declared by each file key, in item key order."""
        found: dict[str, list[UUID]] = {key: [] for key in keys}
        seen: dict[str, set[UUID]] = {key: set() for key in keys}
        if len(keys) == 0:
            return found
        statement = (
            select(AgentDefinitionFileRow.key, AgentDefinitionItemRow.agent_id)
            .join(
                AgentDefinitionItemRow,
                AgentDefinitionItemRow.file_id == AgentDefinitionFileRow.id,
            )
            .where(AgentDefinitionFileRow.key.in_(keys))
            .where(AgentDefinitionItemRow.kind == DefinitionKind.AGENT.value)
            .where(AgentDefinitionItemRow.agent_id.is_not(None))
            .order_by(AgentDefinitionFileRow.key, AgentDefinitionItemRow.item_key)
        )
        for record in self._session.execute(statement):
            key: str = record[0]
            agent_id: UUID | None = record[1]
            bucket: list[UUID] | None = found.get(key)
            used: set[UUID] | None = seen.get(key)
            if agent_id is None or bucket is None or used is None or agent_id in used:
                continue
            used.add(agent_id)
            bucket.append(agent_id)
        return found

    def items_for(self, file_id: UUID) -> list[AgentDefinitionItemRow]:
        """Return the declarations of one file in key order."""
        statement = (
            select(AgentDefinitionItemRow)
            .where(AgentDefinitionItemRow.file_id == file_id)
            .order_by(AgentDefinitionItemRow.item_key)
        )
        return list(self._session.scalars(statement))

    def schema_parents(self) -> list[AgentDefinitionItemRow]:
        """Applied schema declarations. Children and removed files are left out."""
        statement = (
            select(AgentDefinitionItemRow)
            .join(
                AgentDefinitionFileRow,
                AgentDefinitionFileRow.id == AgentDefinitionItemRow.file_id,
            )
            .where(
                AgentDefinitionItemRow.kind == DefinitionKind.AGENT.value,
                AgentDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
                AgentDefinitionItemRow.agent_id.is_(None),
                AgentDefinitionFileRow.status != DefinitionFileStatus.REMOVED.value,
            )
            .order_by(AgentDefinitionItemRow.item_key)
        )
        found: list[AgentDefinitionItemRow] = []
        row: AgentDefinitionItemRow
        for row in self._session.scalars(statement):
            source: object = row.declaration.get("source")
            if isinstance(source, dict) and source.get("kind") == "schema":
                found.append(row)
        return found

    def unresolved(self) -> list[AgentDefinitionItemRow]:
        """Return unresolved items."""
        statement = select(AgentDefinitionItemRow).where(
            AgentDefinitionItemRow.status == DefinitionItemStatus.UNRESOLVED.value,
        )
        rows: list[AgentDefinitionItemRow] = list(self._session.scalars(statement))
        rows.sort(key=_item_sort)
        return rows

    def applied_agent_ids(self) -> set[UUID]:
        """Return agents an applied agent declaration still names."""
        statement = select(AgentDefinitionItemRow.agent_id).where(
            AgentDefinitionItemRow.kind == DefinitionKind.AGENT.value,
            AgentDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
            AgentDefinitionItemRow.agent_id.is_not(None),
        )
        found: set[UUID] = set()
        agent_id: UUID | None
        for agent_id in self._session.scalars(statement):
            if agent_id is not None:
                found.add(agent_id)
        return found

    def other_applied_claim(self, item: AgentDefinitionItemRow) -> bool:
        """True when another applied item still names this agent."""
        if item.agent_id is None:
            return False
        statement = select(AgentDefinitionItemRow.id).where(
            AgentDefinitionItemRow.id != item.id,
            AgentDefinitionItemRow.agent_id == item.agent_id,
            AgentDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
        )
        found: UUID | None = self._session.scalar(statement)
        return found is not None

    def delete_item(self, row: AgentDefinitionItemRow) -> None:
        """Delete one declaration. The caller commits."""
        self._session.delete(row)
        self._session.flush()


def _item_sort(row: AgentDefinitionItemRow) -> tuple[int, str]:
    rank: int = KIND_RANK.get(row.kind, 9)
    return (rank, row.item_key)
