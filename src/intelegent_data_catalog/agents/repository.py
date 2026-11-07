"""Reads and writes agent rows and the agent search."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Float, Select, and_, case, cast, func, or_, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql import Subquery
from sqlalchemy.sql.elements import ColumnElement, SQLCoreOperations
from sqlalchemy.sql.selectable import SelectBase

from intelegent_data_catalog.agents.models import (
    AgentListItem,
    AgentQuery,
    ContextState,
)
from intelegent_data_catalog.agents.naming import AgentSlugs
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.enums import (
    AgentKind,
    AgentRefreshMode,
    AgentStatus,
    CatalogJobKind,
    CatalogJobStatus,
)
from intelegent_data_catalog.persistence.models import (
    AgentRow,
    CatalogJobRow,
    CatalogTableRow,
    EntityConfigurationRow,
)

_DEFAULT_INTERVAL: int = 600


class AgentRepository:
    """Agent persistence. The caller owns the session except where a method commits."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    @property
    def session(self) -> Session:
        """Session this repository writes."""
        return self._session

    def get(self, agent_id: UUID) -> AgentRow | None:
        """Return one agent."""
        return self._session.get(AgentRow, agent_id)

    def by_table_id(self, table_id: UUID) -> AgentRow | None:
        """Return the agent linked to a catalog table."""
        table: CatalogTableRow | None = self._session.get(CatalogTableRow, table_id)
        if table is None or table.agent_id is None:
            return None
        return self.get(table.agent_id)

    def by_dag_row_id(self, dag_row_id: UUID) -> AgentRow | None:
        """Return the agent linked to an Airflow DAG snapshot."""
        from intelegent_data_catalog.persistence.models import AirflowDagRow

        dag: AirflowDagRow | None = self._session.get(AirflowDagRow, dag_row_id)
        if dag is None or dag.agent_id is None:
            return None
        return self.get(dag.agent_id)

    def by_node_id(self, node_id: UUID) -> AgentRow | None:
        """Return the agent linked to a workspace file."""
        from intelegent_data_catalog.persistence.models import WorkspaceNodeRow

        node: WorkspaceNodeRow | None = self._session.get(WorkspaceNodeRow, node_id)
        if node is None or node.agent_id is None:
            return None
        return self.get(node.agent_id)

    def add(self, row: AgentRow) -> AgentRow:
        """Insert an agent. The caller commits."""
        self._session.add(row)
        self._session.flush()
        return row

    def delete(self, row: AgentRow) -> None:
        """Delete an agent. Memberships cascade. The caller commits."""
        self._session.delete(row)
        self._session.flush()










    def search(self, query: AgentQuery, access: AccessContext) -> tuple[list[AgentListItem], int]:
        """Return one page of visible agents and the unpaged total."""
        filters: list[ColumnElement[bool]] = self._filters(query, access)
        total: int = self._total(filters)
        rows: list[AgentRow] = self._page(query, filters)
        return self._items(rows), total

    def slug(
        self,
        workspace_id: UUID,
        owner_user_id: UUID | None,
        raw: str,
        current_id: UUID | None = None,
    ) -> str:
        """Allocate a unique slug."""
        return AgentSlugs(self._session).claim(workspace_id, owner_user_id, raw, current_id)

    def shared_workspace_id(self) -> UUID:
        """Workspace used for catalog tables and DAGs that are not personal files."""
        return SHARED_WORKSPACE_ID


    def _filters(self, query: AgentQuery, access: AccessContext) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = []
        if not access.principal.is_admin:
            filters.append(
                or_(
                    AgentRow.owner_user_id.is_(None),
                    AgentRow.owner_user_id == access.user_id,
                ),
            )
            if len(access.member_workspace_ids) > 0:
                filters.append(AgentRow.workspace_id.in_(access.member_workspace_ids))
        if query.workspace_id is not None:
            filters.append(AgentRow.workspace_id == query.workspace_id)
        elif access.workspace_id is not None and not access.principal.is_admin:
            filters.append(AgentRow.workspace_id == access.workspace_id)
        if query.owner == "me":
            filters.append(AgentRow.owner_user_id == access.user_id)
        elif query.owner == "shared":
            filters.append(AgentRow.owner_user_id.is_(None))
        if len(query.kinds) > 0:
            filters.append(AgentRow.kind.in_([kind.value for kind in query.kinds]))
        if len(query.formats) > 0:
            stored_format: ColumnElement[str] = func.lower(
                AgentRow.properties["table_format"].as_string()
            )
            wanted: list[str] = [item.lower() for item in query.formats if item != ""]
            if len(wanted) > 0:
                filters.append(stored_format.in_(wanted))
        if query.status is not None:
            filters.append(AgentRow.status == query.status.value)
        if query.has_context is True:
            filters.append(AgentRow.context.is_not(None))
        elif query.has_context is False:
            filters.append(AgentRow.context.is_(None))
        if query.q is not None and query.q.strip() != "":
            pattern: str = f"%{query.q.strip()}%"
            filters.append(
                or_(
                    AgentRow.name.ilike(pattern),
                    AgentRow.description.ilike(pattern),
                    AgentRow.context.ilike(pattern),
                ),
            )
        if query.refresh_mode is not None:
            filters.append(
                AgentRow.id.in_(
                    select(EntityConfigurationRow.agent_id).where(
                        EntityConfigurationRow.refresh_mode == query.refresh_mode.value,
                        EntityConfigurationRow.agent_id.is_not(None),
                    ),
                ),
            )
        return filters


    def _total(self, filters: list[ColumnElement[bool]]) -> int:
        statement = select(func.count()).select_from(AgentRow)
        if len(filters) > 0:
            statement = statement.where(and_(*filters))
        counted: object = self._session.scalar(statement)
        if isinstance(counted, int):
            return counted
        return 0

    def _page(self, query: AgentQuery, filters: list[ColumnElement[bool]]) -> list[AgentRow]:
        dialect: str = self._session.get_bind().dialect.name
        builds = _build_times()
        modes = _refresh_modes()
        freshness = _freshness_score(
            builds.c.last_built_at,
            modes.c.interval_seconds,
            modes.c.refresh_mode,
            dialect,
        )
        context_state = _context_state_expr()
        statement = (
            select(AgentRow)
            .outerjoin(builds, builds.c.agent_id == AgentRow.id)
            .outerjoin(modes, modes.c.agent_id == AgentRow.id)
        )
        if len(filters) > 0:
            statement = statement.where(and_(*filters))
        descending: bool = query.order != "asc"
        match query.sort:
            case "name":
                statement = _order_page(statement, AgentRow.name, descending=descending)
            case "kind":
                statement = _order_page(statement, AgentRow.kind, descending=descending)
            case "updated_at":
                statement = _order_page(statement, AgentRow.updated_at, descending=descending)
            case "last_built_at":
                statement = _order_page(statement, builds.c.last_built_at, descending=descending)
            case "freshness_score":
                statement = _order_page(statement, freshness, descending=descending)
            case "context_state":
                statement = _order_page(statement, context_state, descending=descending)
        statement = statement.limit(query.limit).offset(query.offset)
        return list(self._session.scalars(statement))

    def _items(self, rows: list[AgentRow]) -> list[AgentListItem]:
        if len(rows) == 0:
            return []
        ids: list[UUID] = [row.id for row in rows]
        built: dict[UUID, datetime] = self._last_built(ids)
        running: set[UUID] = self._running(ids)
        modes: dict[UUID, tuple[str, int]] = self._modes(ids)
        now: datetime = utc_now()
        items: list[AgentListItem] = []
        row: AgentRow
        for row in rows:
            fallback: tuple[str, int] = (AgentRefreshMode.INTERVAL.value, _DEFAULT_INTERVAL)
            mode_name, interval = modes.get(row.id, fallback)
            last_built: datetime | None = built.get(row.id)
            items.append(
                AgentListItem(
                    id=row.id,
                    kind=AgentKind(row.kind),
                    name=row.name,
                    slug=row.slug,
                    description=row.description,
                    workspace_id=row.workspace_id,
                    owner_user_id=row.owner_user_id,
                    status=AgentStatus(row.status),
                    subtitle=_subtitle(row.properties),
                    table_format=_table_format(row.properties),
                    last_built_at=last_built,
                    context_state=_state(row),
                    freshness_score=_score(mode_name, interval, last_built, now),
                    interval_seconds=interval,
                    build_running=row.id in running,
                    refresh_mode=AgentRefreshMode(mode_name),
                    updated_at=row.updated_at,
                ),
            )
        return items


    def _last_built(self, agent_ids: list[UUID]) -> dict[UUID, datetime]:
        found: dict[UUID, datetime] = {}
        agent_builds = (
            select(CatalogJobRow.subject_id, func.max(CatalogJobRow.created_at))
            .where(
                CatalogJobRow.subject_id.in_(agent_ids),
                CatalogJobRow.kind == CatalogJobKind.AGENT.value,
                CatalogJobRow.lane == "",
                CatalogJobRow.status == CatalogJobStatus.SUCCEEDED.value,
            )
            .group_by(CatalogJobRow.subject_id)
        )
        for agent_id, built_at in self._session.execute(agent_builds):
            _keep_latest(found, agent_id, built_at)
        table_builds = (
            select(CatalogTableRow.agent_id, func.max(CatalogJobRow.created_at))
            .select_from(CatalogJobRow)
            .join(CatalogTableRow, CatalogTableRow.id == CatalogJobRow.subject_id)
            .where(
                CatalogTableRow.agent_id.in_(agent_ids),
                CatalogJobRow.kind == CatalogJobKind.KNOWLEDGE.value,
                CatalogJobRow.lane == "",
                CatalogJobRow.status == CatalogJobStatus.SUCCEEDED.value,
            )
            .group_by(CatalogTableRow.agent_id)
        )
        for table_agent_id, table_built_at in self._session.execute(table_builds):
            _keep_latest(found, table_agent_id, table_built_at)
        return found

    def _running(self, agent_ids: list[UUID]) -> set[UUID]:
        found: set[UUID] = set()
        agent_runs = select(CatalogJobRow.subject_id).where(
            CatalogJobRow.subject_id.in_(agent_ids),
            CatalogJobRow.kind == CatalogJobKind.AGENT.value,
            CatalogJobRow.lane == "",
            CatalogJobRow.status == CatalogJobStatus.RUNNING.value,
        )
        for agent_id in self._session.scalars(agent_runs):
            found.add(agent_id)
        table_runs = (
            select(CatalogTableRow.agent_id)
            .select_from(CatalogJobRow)
            .join(CatalogTableRow, CatalogTableRow.id == CatalogJobRow.subject_id)
            .where(
                CatalogTableRow.agent_id.in_(agent_ids),
                CatalogJobRow.kind == CatalogJobKind.KNOWLEDGE.value,
                CatalogJobRow.lane == "",
                CatalogJobRow.status == CatalogJobStatus.RUNNING.value,
            )
        )
        for table_agent_id in self._session.scalars(table_runs):
            if table_agent_id is not None:
                found.add(table_agent_id)
        return found

    def _modes(self, agent_ids: list[UUID]) -> dict[UUID, tuple[str, int]]:
        statement = select(EntityConfigurationRow).where(
            EntityConfigurationRow.agent_id.in_(agent_ids),
        )
        found: dict[UUID, tuple[str, int]] = {}
        row: EntityConfigurationRow
        for row in self._session.scalars(statement):
            if row.agent_id is None:
                continue
            found[row.agent_id] = (row.refresh_mode, row.interval_seconds)
        return found


def _build_source() -> SelectBase:
    """Agent builds plus table builds, both keyed by agent id."""
    agent_jobs = select(
        CatalogJobRow.subject_id.label("agent_id"),
        CatalogJobRow.created_at.label("built_at"),
        CatalogJobRow.status.label("status"),
    ).where(
        CatalogJobRow.kind == CatalogJobKind.AGENT.value,
        CatalogJobRow.lane == "",
    )
    table_jobs = (
        select(
            CatalogTableRow.agent_id.label("agent_id"),
            CatalogJobRow.created_at.label("built_at"),
            CatalogJobRow.status.label("status"),
        )
        .select_from(CatalogJobRow)
        .join(CatalogTableRow, CatalogTableRow.id == CatalogJobRow.subject_id)
        .where(
            CatalogJobRow.kind == CatalogJobKind.KNOWLEDGE.value,
            CatalogJobRow.lane == "",
            CatalogTableRow.agent_id.is_not(None),
        )
    )
    return union_all(agent_jobs, table_jobs)


def _build_times() -> Subquery:
    source = _build_source().subquery()
    return (
        select(
            source.c.agent_id.label("agent_id"),
            func.max(source.c.built_at).label("last_built_at"),
        )
        .where(source.c.status == CatalogJobStatus.SUCCEEDED.value)
        .group_by(source.c.agent_id)
        .subquery()
    )




def _refresh_modes() -> Subquery:
    return (
        select(
            EntityConfigurationRow.agent_id.label("agent_id"),
            EntityConfigurationRow.refresh_mode.label("refresh_mode"),
            EntityConfigurationRow.interval_seconds.label("interval_seconds"),
        )
        .where(EntityConfigurationRow.agent_id.is_not(None))
        .subquery()
    )


def _freshness_score(
    last_built: ColumnElement[datetime | None],
    interval_seconds: ColumnElement[int | None],
    refresh_mode: ColumnElement[str | None],
    dialect: str,
) -> ColumnElement[int]:
    age = case(
        (last_built.is_(None), None),
        else_=_age_seconds(last_built, dialect),
    )
    interval = func.coalesce(interval_seconds, _DEFAULT_INTERVAL)
    elapsed = age / interval * 100
    raw = 100 - elapsed
    clamped = case(
        (raw < 0, 0),
        (raw > 100, 100),
        else_=raw,
    )
    return case(
        (last_built.is_(None), 0),
        (refresh_mode == AgentRefreshMode.MANUAL.value, 100),
        else_=clamped,
    )


def _age_seconds(
    last_built: ColumnElement[datetime | None],
    dialect: str,
) -> ColumnElement[float]:
    if dialect == "postgresql":
        epoch: ColumnElement[int] = func.extract("epoch", func.now() - last_built)
        return cast(epoch, Float())
    return (func.julianday("now") - func.julianday(last_built)) * 86400.0


def _order_page(
    statement: Select[AgentRow],
    column: SQLCoreOperations[object],
    *,
    descending: bool,
) -> Select[AgentRow]:
    """Sort one page, with id as a stable tie break."""
    if descending:
        return statement.order_by(column.desc(), AgentRow.id.desc())
    return statement.order_by(column.asc(), AgentRow.id.asc())


def _context_state_expr() -> ColumnElement[str]:
    return case(
        (AgentRow.context.is_(None), "none"),
        (AgentRow.context_origin == "USER", "user"),
        else_="llm",
    )


def _state(row: AgentRow) -> ContextState:
    if row.context is None or row.context.strip() == "":
        return "none"
    if row.context_origin == "USER":
        return "user"
    return "llm"


def _score(mode: str, interval: int, last_built: datetime | None, now: datetime) -> int:
    if last_built is None:
        return 0
    if mode == AgentRefreshMode.MANUAL.value:
        return 100
    span: int = interval if interval > 0 else _DEFAULT_INTERVAL
    age: float = (_aware(now) - _aware(last_built)).total_seconds()
    raw: int = 100 - int(age / span * 100)
    return max(0, min(100, raw))


def _table_format(properties: dict[str, object]) -> str | None:
    value: object = properties.get("table_format")
    if isinstance(value, str) and value != "":
        return value
    return None


def _subtitle(properties: dict[str, object]) -> str | None:
    path: object = properties.get("path")
    if isinstance(path, str) and path != "":
        return path
    source_key: object = properties.get("source_key")
    if isinstance(source_key, str) and source_key != "":
        return source_key
    dag_id: object = properties.get("dag_id")
    if isinstance(dag_id, str) and dag_id != "":
        return dag_id
    return None


def _keep_latest(
    found: dict[UUID, datetime],
    agent_id: UUID | None,
    built_at: datetime | None,
) -> None:
    """Keep the newer of an agent build and a table build."""
    if agent_id is None or built_at is None:
        return
    stamp: datetime = _aware(built_at)
    current: datetime | None = found.get(agent_id)
    if current is None or stamp > current:
        found[agent_id] = stamp


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
