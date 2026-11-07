"""Keep an agent row next to its catalog table, DAG, Airflow instance, or workspace file."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, object_session

from intelegent_data_catalog.agents.definitions.models import AgentSpec, SchemaSourceRef
from intelegent_data_catalog.agents.naming import AgentSlugs
from intelegent_data_catalog.agents.repository import AgentRepository
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.enums import (
    AgentKind,
    AgentStatus,
    AirflowInstanceStatus,
)
from intelegent_data_catalog.persistence.models import (
    AgentRow,
    AirflowDagRow,
    AirflowInstanceRow,
    CatalogTableRow,
    WorkspaceNodeRow,
)


class AgentSync:
    """Creates and updates the agent for a source row. The caller commits."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session
        self._repository: AgentRepository = AgentRepository(session)
        self._slugs: AgentSlugs = AgentSlugs(session)

    @staticmethod
    def for_row(row: object) -> AgentSync | None:
        """Build a sync from the row's session. None when the row is detached."""
        session: Session | None = object_session(row)
        if session is None:
            return None
        return AgentSync(session)

    def ensure_table(self, row: CatalogTableRow) -> AgentRow:
        """Create or refresh the table agent. Membership is not added here."""
        agent: AgentRow | None = self._existing(row.agent_id)
        now: datetime = utc_now()
        known: set[str] = {item.value for item in AgentStatus}
        status: str = row.status if row.status in known else AgentStatus.ACTIVE.value
        properties: dict[str, object] = {
            "source_key": row.source_key,
            "table_format": row.table_format,
            "table_id": str(row.id),
        }
        if agent is None:
            agent = self._insert(
                kind=AgentKind.TABLE,
                name=row.name,
                raw_slug=row.source_key or row.name,
                description=row.description,
                context=row.context,
                context_origin=row.context_origin,
                workspace_id=SHARED_WORKSPACE_ID,
                owner_user_id=None,
                created_by=None,
                properties=properties,
                status=status,
                last_synced_at=row.last_synced_at,
                now=now,
            )
            row.agent_id = agent.id
        else:
            agent.name = _clip(row.name)
            agent.description = row.description
            agent.status = status
            agent.properties = properties
            agent.last_synced_at = row.last_synced_at
            agent.updated_at = now
        self._session.flush()
        return agent

    def ensure_table_alias(
        self,
        row: CatalogTableRow,
        existing: AgentRow | None,
        raw_slug: str,
    ) -> AgentRow:
        """Create or refresh a table agent that does not own the catalog row.

        ``catalog_tables.agent_id`` stays on the primary agent.
        """
        now: datetime = utc_now()
        known: set[str] = {item.value for item in AgentStatus}
        status: str = row.status if row.status in known else AgentStatus.ACTIVE.value
        properties: dict[str, object] = {
            "source_key": row.source_key,
            "table_format": row.table_format,
            "table_id": str(row.id),
        }
        if row.agent_id is not None:
            properties["alias_of"] = str(row.agent_id)
        if existing is None:
            created: AgentRow = self._insert(
                kind=AgentKind.TABLE,
                name=row.name,
                raw_slug=raw_slug,
                description=row.description,
                context=row.context,
                context_origin=row.context_origin,
                workspace_id=SHARED_WORKSPACE_ID,
                owner_user_id=None,
                created_by=None,
                properties=properties,
                status=status,
                last_synced_at=row.last_synced_at,
                now=now,
            )
            self._session.flush()
            return created
        existing.name = _clip(row.name)
        existing.description = row.description
        existing.status = status
        existing.properties = properties
        existing.last_synced_at = row.last_synced_at
        existing.updated_at = now
        self._session.flush()
        return existing

    def ensure_schema(self, spec: AgentSpec, existing: AgentRow | None) -> AgentRow:
        """Create or refresh the one agent for a schema declaration.

        Catalog tables stay unowned. ``catalog_tables.agent_id`` is not set.
        """
        if not isinstance(spec.source, SchemaSourceRef):
            raise ValueError("schema agent requires a schema source")
        source: SchemaSourceRef = spec.source
        now: datetime = utc_now()
        properties: dict[str, object] = {
            "catalog": source.catalog,
            "namespace": source.namespace,
            "data_source": source.data_source,
            "include": source.include,
            "exclude": list(source.exclude),
        }
        if existing is None:
            created: AgentRow = self._insert(
                kind=AgentKind.SCHEMA,
                name=spec.key,
                raw_slug=spec.key,
                description=spec.description,
                context=None,
                context_origin=None,
                workspace_id=SHARED_WORKSPACE_ID,
                owner_user_id=None,
                created_by=None,
                properties=properties,
                status=AgentStatus.ACTIVE.value,
                last_synced_at=None,
                now=now,
            )
            self._session.flush()
            return created
        existing.kind = AgentKind.SCHEMA.value
        existing.name = _clip(spec.key)
        existing.properties = properties
        existing.status = AgentStatus.ACTIVE.value
        existing.updated_at = now
        self._session.flush()
        return existing

    def delete_table(self, row: CatalogTableRow) -> None:
        """Delete the agent linked to a catalog table."""
        self._delete_linked(row.agent_id)

    def ensure_dag(self, row: AirflowDagRow) -> AgentRow:
        """Create or refresh the DAG agent. Stale DAGs are marked missing."""
        agent: AgentRow | None = self._existing(row.agent_id)
        now: datetime = row.last_seen_at
        label: str = row.dag_id if row.display_name is None else row.display_name
        status: str = AgentStatus.MISSING.value if row.is_stale else AgentStatus.ACTIVE.value
        properties: dict[str, object] = {
            "dag_id": row.dag_id,
            "instance_id": str(row.instance_id),
        }
        if agent is None:
            agent = self._insert(
                kind=AgentKind.AIRFLOW_DAG,
                name=label,
                raw_slug=f"{row.instance_id}-{row.dag_id}",
                description=row.description,
                context=None,
                context_origin=None,
                workspace_id=SHARED_WORKSPACE_ID,
                owner_user_id=None,
                created_by=None,
                properties=properties,
                status=status,
                last_synced_at=row.last_seen_at,
                now=now,
            )
            row.agent_id = agent.id
        else:
            agent.name = _clip(label)
            agent.description = row.description
            agent.status = status
            agent.properties = properties
            agent.last_synced_at = row.last_seen_at
            agent.updated_at = utc_now()
        self._session.flush()
        return agent

    def ensure_instance(self, row: AirflowInstanceRow) -> AgentRow:
        """Create or refresh the instance agent. Definition keys on the agent stay."""
        agent: AgentRow | None = self._existing(row.agent_id)
        now: datetime = utc_now()
        status: str = (
            AgentStatus.DEPRECATED.value
            if row.status == AirflowInstanceStatus.DISABLED.value
            else AgentStatus.ACTIVE.value
        )
        properties: dict[str, object] = {
            "instance_id": str(row.id),
            "slug": row.slug,
        }
        if agent is None:
            agent = self._insert(
                kind=AgentKind.AIRFLOW_INSTANCE,
                name=row.name,
                raw_slug=row.slug or row.name,
                description=row.description,
                context=row.context,
                context_origin=row.context_origin,
                workspace_id=SHARED_WORKSPACE_ID,
                owner_user_id=None,
                created_by=None,
                properties=properties,
                status=status,
                last_synced_at=row.last_synced_at,
                now=now,
            )
            row.agent_id = agent.id
        else:
            agent.name = _clip(row.name)
            agent.description = row.description
            agent.status = status
            agent.properties = _keep_declared(agent.properties, properties)
            agent.last_synced_at = row.last_synced_at
            agent.updated_at = now
        self._session.flush()
        return agent

    def delete_instance(self, row: AirflowInstanceRow) -> None:
        """Delete the agent linked to an Airflow instance. The instance row stays."""
        agent_id: UUID | None = row.agent_id
        row.agent_id = None
        self._session.flush()
        self._delete_linked(agent_id)

    def ensure_file(self, row: WorkspaceNodeRow) -> AgentRow | None:
        """Create or refresh the file or notebook agent. Folders are ignored."""
        if row.kind != "FILE":
            return None
        agent: AgentRow | None = self._existing(row.agent_id)
        now: datetime = utc_now()
        kind: AgentKind = (
            AgentKind.NOTEBOOK if row.name.lower().endswith(".ipynb") else AgentKind.FILE
        )
        properties: dict[str, object] = {
            "content_type": row.content_type,
            "path": row.path,
            "node_id": str(row.id),
        }
        if agent is None:
            agent = self._insert(
                kind=kind,
                name=row.name,
                raw_slug=row.path,
                description=None,
                context=None,
                context_origin=None,
                workspace_id=row.workspace_id,
                owner_user_id=row.owner_user_id,
                created_by=row.created_by,
                properties=properties,
                status=AgentStatus.ACTIVE.value,
                last_synced_at=None,
                now=now,
            )
            row.agent_id = agent.id
        else:
            agent.kind = kind.value
            agent.name = _clip(row.name)
            agent.workspace_id = row.workspace_id
            agent.owner_user_id = row.owner_user_id
            agent.properties = properties
            agent.updated_at = now
        self._session.flush()
        return agent

    def delete_file(self, row: WorkspaceNodeRow) -> None:
        """Delete the agent linked to a workspace node."""
        self._delete_linked(row.agent_id)

    def _insert(
        self,
        *,
        kind: AgentKind,
        name: str,
        raw_slug: str,
        description: str | None,
        context: str | None,
        context_origin: str | None,
        workspace_id: UUID,
        owner_user_id: UUID | None,
        created_by: UUID | None,
        properties: dict[str, object],
        status: str,
        last_synced_at: datetime | None,
        now: datetime,
    ) -> AgentRow:
        slug: str = self._slugs.claim(workspace_id, owner_user_id, raw_slug)
        created: AgentRow = AgentRow(
            id=uuid4(),
            kind=kind.value,
            name=_clip(name),
            slug=slug,
            description=description,
            context=context,
            context_origin=context_origin,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            created_by=created_by,
            properties=properties,
            status=status,
            last_synced_at=last_synced_at,
            created_at=now,
            updated_at=now,
        )
        return self._repository.add(created)

    def _existing(self, agent_id: UUID | None) -> AgentRow | None:
        if agent_id is None:
            return None
        return self._repository.get(agent_id)

    def _delete_linked(self, agent_id: UUID | None) -> None:
        if agent_id is None:
            return
        agent: AgentRow | None = self._repository.get(agent_id)
        if agent is None:
            return
        self._repository.delete(agent)


def _keep_declared(current: dict[str, object], properties: dict[str, object]) -> dict[str, object]:
    """Keep definition overlay keys when the source row is refreshed."""
    merged: dict[str, object] = dict(properties)
    key: str
    for key in ("definition", "declared", "tags"):
        if key in current:
            merged[key] = current[key]
    return merged


def _clip(value: str) -> str:
    if len(value) <= 255:
        return value
    return value[:255]
