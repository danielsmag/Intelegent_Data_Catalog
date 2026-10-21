"""Reads and writes asset rows, memberships, and the asset search."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, case, func, or_, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql import Subquery
from sqlalchemy.sql.elements import ColumnElement

from intelegent_data_catalog.assets.models import (
    AssetListItem,
    AssetQuery,
    AssetTargetRef,
    ContextState,
)
from intelegent_data_catalog.assets.naming import AssetSlugs
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.enums import (
    AssetKind,
    AssetRefreshMode,
    AssetStatus,
    CatalogJobKind,
    CatalogJobStatus,
)
from intelegent_data_catalog.persistence.models import (
    AssetMembershipRow,
    AssetRow,
    CatalogJobRow,
    CatalogTableRow,
    EntityConfigurationRow,
    ScopeRow,
    SpaceRow,
)

_DEFAULT_INTERVAL: int = 600


class AssetRepository:
    """Asset persistence. The caller owns the session except where a method commits."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    @property
    def session(self) -> Session:
        """Session this repository writes."""
        return self._session

    def get(self, asset_id: UUID) -> AssetRow | None:
        """Return one asset."""
        return self._session.get(AssetRow, asset_id)

    def by_table_id(self, table_id: UUID) -> AssetRow | None:
        """Return the asset linked to a catalog table."""
        table: CatalogTableRow | None = self._session.get(CatalogTableRow, table_id)
        if table is None or table.asset_id is None:
            return None
        return self.get(table.asset_id)

    def by_dag_row_id(self, dag_row_id: UUID) -> AssetRow | None:
        """Return the asset linked to an Airflow DAG snapshot."""
        from intelegent_data_catalog.persistence.models import AirflowDagRow

        dag: AirflowDagRow | None = self._session.get(AirflowDagRow, dag_row_id)
        if dag is None or dag.asset_id is None:
            return None
        return self.get(dag.asset_id)

    def by_node_id(self, node_id: UUID) -> AssetRow | None:
        """Return the asset linked to a workspace file."""
        from intelegent_data_catalog.persistence.models import WorkspaceNodeRow

        node: WorkspaceNodeRow | None = self._session.get(WorkspaceNodeRow, node_id)
        if node is None or node.asset_id is None:
            return None
        return self.get(node.asset_id)

    def add(self, row: AssetRow) -> AssetRow:
        """Insert an asset. The caller commits."""
        self._session.add(row)
        self._session.flush()
        return row

    def delete(self, row: AssetRow) -> None:
        """Delete an asset. Memberships cascade. The caller commits."""
        self._session.delete(row)
        self._session.flush()

    def memberships_for_target(
        self,
        *,
        scope_id: UUID | None,
        space_id: UUID | None,
    ) -> list[AssetMembershipRow]:
        """Return memberships on one scope or one space."""
        statement: Select[tuple[AssetMembershipRow]] = select(AssetMembershipRow)
        if scope_id is not None:
            statement = statement.where(AssetMembershipRow.scope_id == scope_id)
        else:
            statement = statement.where(AssetMembershipRow.space_id == space_id)
        return list(self._session.scalars(statement))

    def memberships(self, asset_id: UUID) -> list[AssetMembershipRow]:
        """Return every space and scope holding this asset."""
        statement: Select[tuple[AssetMembershipRow]] = select(AssetMembershipRow).where(
            AssetMembershipRow.asset_id == asset_id,
        )
        return list(self._session.scalars(statement))

    def membership_for(
        self,
        asset_id: UUID,
        *,
        scope_id: UUID | None,
        space_id: UUID | None,
    ) -> AssetMembershipRow | None:
        """Return the membership for one target, or none."""
        statement: Select[tuple[AssetMembershipRow]] = select(AssetMembershipRow).where(
            AssetMembershipRow.asset_id == asset_id,
        )
        if scope_id is not None:
            statement = statement.where(AssetMembershipRow.scope_id == scope_id)
        else:
            statement = statement.where(AssetMembershipRow.space_id == space_id)
        return self._session.scalar(statement)

    def attach(
        self,
        asset_id: UUID,
        *,
        scope_id: UUID | None,
        space_id: UUID | None,
    ) -> tuple[AssetMembershipRow, bool]:
        """Attach an asset. The flag is false when the membership already exists."""
        existing: AssetMembershipRow | None = self.membership_for(
            asset_id,
            scope_id=scope_id,
            space_id=space_id,
        )
        if existing is not None:
            return existing, False
        created: AssetMembershipRow = AssetMembershipRow(
            id=uuid4(),
            asset_id=asset_id,
            scope_id=scope_id,
            space_id=space_id,
            created_at=utc_now(),
        )
        self._session.add(created)
        self._session.flush()
        return created, True

    def detach(self, row: AssetMembershipRow) -> None:
        """Remove one membership. The caller commits."""
        self._session.delete(row)
        self._session.flush()

    def list_for_scope(self, scope_id: UUID) -> list[AssetRow]:
        """Return assets attached to one scope."""
        statement: Select[tuple[AssetRow]] = (
            select(AssetRow)
            .join(AssetMembershipRow, AssetMembershipRow.asset_id == AssetRow.id)
            .where(AssetMembershipRow.scope_id == scope_id)
            .order_by(AssetRow.kind, AssetRow.name)
        )
        return list(self._session.scalars(statement))

    def list_for_space(self, space_id: UUID) -> list[AssetRow]:
        """Return assets attached to the space or to one of its scopes."""
        scope_ids: Select[tuple[UUID]] = select(ScopeRow.id).where(ScopeRow.space_id == space_id)
        statement: Select[tuple[AssetRow]] = (
            select(AssetRow)
            .join(AssetMembershipRow, AssetMembershipRow.asset_id == AssetRow.id)
            .where(
                or_(
                    AssetMembershipRow.space_id == space_id,
                    AssetMembershipRow.scope_id.in_(scope_ids),
                ),
            )
            .order_by(AssetRow.kind, AssetRow.name)
            .distinct()
        )
        return list(self._session.scalars(statement))

    def summaries_for_scope(self, scope_id: UUID) -> list[tuple[UUID, str, str, str | None]]:
        """Non-table assets on a scope: id, kind, name, summary from the snapshot payload."""
        return self._summaries(AssetMembershipRow.scope_id == scope_id)

    def summaries_for_space(self, space_id: UUID) -> list[tuple[UUID, str, str, str | None]]:
        """Non-table assets on a space or its scopes."""
        scope_ids: Select[tuple[UUID]] = select(ScopeRow.id).where(ScopeRow.space_id == space_id)
        return self._summaries(
            or_(
                AssetMembershipRow.space_id == space_id,
                AssetMembershipRow.scope_id.in_(scope_ids),
            ),
        )

    def search(self, query: AssetQuery, access: AccessContext) -> tuple[list[AssetListItem], int]:
        """Return one page of visible assets and the unpaged total."""
        filters: list[ColumnElement[bool]] = self._filters(query, access)
        total: int = self._total(filters)
        rows: list[AssetRow] = self._page(query, filters)
        return self._items(rows), total

    def slug(
        self,
        workspace_id: UUID,
        owner_user_id: UUID | None,
        raw: str,
        current_id: UUID | None = None,
    ) -> str:
        """Allocate a unique slug."""
        return AssetSlugs(self._session).claim(workspace_id, owner_user_id, raw, current_id)

    def shared_workspace_id(self) -> UUID:
        """Workspace used for catalog tables and DAGs that are not personal files."""
        return SHARED_WORKSPACE_ID

    def _summaries(
        self,
        predicate: ColumnElement[bool],
    ) -> list[tuple[UUID, str, str, str | None]]:
        from intelegent_data_catalog.persistence.models import AssetKnowledgeContextRow

        statement = (
            select(AssetRow, AssetKnowledgeContextRow.payload)
            .join(AssetMembershipRow, AssetMembershipRow.asset_id == AssetRow.id)
            .outerjoin(
                AssetKnowledgeContextRow,
                AssetKnowledgeContextRow.asset_id == AssetRow.id,
            )
            .where(predicate, AssetRow.kind != AssetKind.TABLE.value)
            .order_by(AssetRow.name)
            .distinct()
        )
        found: list[tuple[UUID, str, str, str | None]] = []
        pair: tuple[AssetRow, object]
        for pair in self._session.execute(statement):
            payload: object = pair[1]
            summary: str | None = None
            if isinstance(payload, dict):
                raw: object = payload.get("summary")
                if isinstance(raw, str) and raw.strip() != "":
                    summary = raw
            found.append((pair[0].id, pair[0].kind, pair[0].name, summary))
        return found

    def _filters(self, query: AssetQuery, access: AccessContext) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = []
        if not access.principal.is_admin:
            filters.append(
                or_(
                    AssetRow.owner_user_id.is_(None),
                    AssetRow.owner_user_id == access.user_id,
                ),
            )
            if len(access.member_workspace_ids) > 0:
                filters.append(AssetRow.workspace_id.in_(access.member_workspace_ids))
        if query.workspace_id is not None:
            filters.append(AssetRow.workspace_id == query.workspace_id)
        elif access.workspace_id is not None and not access.principal.is_admin:
            filters.append(AssetRow.workspace_id == access.workspace_id)
        if query.owner == "me":
            filters.append(AssetRow.owner_user_id == access.user_id)
        elif query.owner == "shared":
            filters.append(AssetRow.owner_user_id.is_(None))
        if len(query.kinds) > 0:
            filters.append(AssetRow.kind.in_([kind.value for kind in query.kinds]))
        if query.status is not None:
            filters.append(AssetRow.status == query.status.value)
        if query.has_context is True:
            filters.append(AssetRow.context.is_not(None))
        elif query.has_context is False:
            filters.append(AssetRow.context.is_(None))
        if query.q is not None and query.q.strip() != "":
            pattern: str = f"%{query.q.strip()}%"
            filters.append(
                or_(
                    AssetRow.name.ilike(pattern),
                    AssetRow.description.ilike(pattern),
                    AssetRow.context.ilike(pattern),
                ),
            )
        if query.scope_id is not None:
            filters.append(AssetRow.id.in_(self._member_ids(scope_id=query.scope_id)))
        if query.space_id is not None:
            scope_ids: Select[tuple[UUID]] = select(ScopeRow.id).where(
                ScopeRow.space_id == query.space_id,
            )
            filters.append(
                or_(
                    AssetRow.id.in_(self._member_ids(space_id=query.space_id)),
                    AssetRow.id.in_(
                        select(AssetMembershipRow.asset_id).where(
                            AssetMembershipRow.scope_id.in_(scope_ids),
                        ),
                    ),
                ),
            )
        if query.refresh_mode is not None:
            filters.append(
                AssetRow.id.in_(
                    select(EntityConfigurationRow.asset_id).where(
                        EntityConfigurationRow.refresh_mode == query.refresh_mode.value,
                        EntityConfigurationRow.asset_id.is_not(None),
                    ),
                ),
            )
        return filters

    def _member_ids(
        self,
        *,
        scope_id: UUID | None = None,
        space_id: UUID | None = None,
    ) -> Select[tuple[UUID]]:
        statement: Select[tuple[UUID]] = select(AssetMembershipRow.asset_id)
        if scope_id is not None:
            return statement.where(AssetMembershipRow.scope_id == scope_id)
        return statement.where(AssetMembershipRow.space_id == space_id)

    def _total(self, filters: list[ColumnElement[bool]]) -> int:
        statement = select(func.count()).select_from(AssetRow)
        if len(filters) > 0:
            statement = statement.where(and_(*filters))
        counted: object = self._session.scalar(statement)
        if isinstance(counted, int):
            return counted
        return 0

    def _page(self, query: AssetQuery, filters: list[ColumnElement[bool]]) -> list[AssetRow]:
        dialect: str = self._session.get_bind().dialect.name
        builds = _build_times()
        counts = _membership_counts()
        modes = _refresh_modes()
        freshness: ColumnElement[int] = _freshness_score(
            builds.c.last_built_at,
            modes.c.interval_seconds,
            modes.c.refresh_mode,
            dialect,
        )
        context_state: ColumnElement[str] = _context_state_expr()
        statement = (
            select(AssetRow)
            .outerjoin(builds, builds.c.asset_id == AssetRow.id)
            .outerjoin(counts, counts.c.asset_id == AssetRow.id)
            .outerjoin(modes, modes.c.asset_id == AssetRow.id)
        )
        if len(filters) > 0:
            statement = statement.where(and_(*filters))
        order_by: ColumnElement[object]
        match query.sort:
            case "name":
                order_by = AssetRow.name
            case "kind":
                order_by = AssetRow.kind
            case "updated_at":
                order_by = AssetRow.updated_at
            case "last_built_at":
                order_by = builds.c.last_built_at
            case "membership_count":
                order_by = func.coalesce(counts.c.membership_count, 0)
            case "freshness_score":
                order_by = freshness
            case "context_state":
                order_by = context_state
        if query.order == "asc":
            statement = statement.order_by(order_by.asc(), AssetRow.id.asc())
        else:
            statement = statement.order_by(order_by.desc(), AssetRow.id.desc())
        statement = statement.limit(query.limit).offset(query.offset)
        return list(self._session.scalars(statement))

    def _items(self, rows: list[AssetRow]) -> list[AssetListItem]:
        if len(rows) == 0:
            return []
        ids: list[UUID] = [row.id for row in rows]
        targets: dict[UUID, tuple[list[AssetTargetRef], list[AssetTargetRef]]] = self._targets(ids)
        built: dict[UUID, datetime] = self._last_built(ids)
        running: set[UUID] = self._running(ids)
        modes: dict[UUID, tuple[str, int]] = self._modes(ids)
        now: datetime = utc_now()
        items: list[AssetListItem] = []
        row: AssetRow
        for row in rows:
            scopes, spaces = targets.get(row.id, ([], []))
            fallback: tuple[str, int] = (AssetRefreshMode.INTERVAL.value, _DEFAULT_INTERVAL)
            mode_name, interval = modes.get(row.id, fallback)
            last_built: datetime | None = built.get(row.id)
            items.append(
                AssetListItem(
                    id=row.id,
                    kind=AssetKind(row.kind),
                    name=row.name,
                    slug=row.slug,
                    description=row.description,
                    workspace_id=row.workspace_id,
                    owner_user_id=row.owner_user_id,
                    status=AssetStatus(row.status),
                    subtitle=_subtitle(row.properties),
                    membership_count=len(scopes) + len(spaces),
                    scopes=scopes,
                    spaces=spaces,
                    last_built_at=last_built,
                    context_state=_state(row),
                    freshness_score=_score(mode_name, interval, last_built, now),
                    build_running=row.id in running,
                    refresh_mode=AssetRefreshMode(mode_name),
                    updated_at=row.updated_at,
                ),
            )
        return items

    def _targets(
        self,
        asset_ids: list[UUID],
    ) -> dict[UUID, tuple[list[AssetTargetRef], list[AssetTargetRef]]]:
        statement = (
            select(AssetMembershipRow, ScopeRow.name, SpaceRow.name)
            .outerjoin(ScopeRow, ScopeRow.id == AssetMembershipRow.scope_id)
            .outerjoin(SpaceRow, SpaceRow.id == AssetMembershipRow.space_id)
            .where(AssetMembershipRow.asset_id.in_(asset_ids))
        )
        found: dict[UUID, tuple[list[AssetTargetRef], list[AssetTargetRef]]] = {}
        record: tuple[AssetMembershipRow, str | None, str | None]
        for record in self._session.execute(statement):
            membership: AssetMembershipRow = record[0]
            scopes, spaces = found.setdefault(membership.asset_id, ([], []))
            if membership.scope_id is not None and record[1] is not None:
                scopes.append(AssetTargetRef(id=membership.scope_id, name=record[1]))
            if membership.space_id is not None and record[2] is not None:
                spaces.append(AssetTargetRef(id=membership.space_id, name=record[2]))
        return found

    def _last_built(self, asset_ids: list[UUID]) -> dict[UUID, datetime]:
        builds = _build_source().subquery()
        statement = (
            select(builds.c.asset_id, func.max(builds.c.built_at))
            .where(
                builds.c.asset_id.in_(asset_ids),
                builds.c.status == CatalogJobStatus.SUCCEEDED.value,
            )
            .group_by(builds.c.asset_id)
        )
        found: dict[UUID, datetime] = {}
        record: tuple[object, object]
        for record in self._session.execute(statement):
            if isinstance(record[0], UUID) and isinstance(record[1], datetime):
                found[record[0]] = _aware(record[1])
        return found

    def _running(self, asset_ids: list[UUID]) -> set[UUID]:
        builds = _build_source().subquery()
        statement = select(builds.c.asset_id).where(
            builds.c.asset_id.in_(asset_ids),
            builds.c.status == CatalogJobStatus.RUNNING.value,
        )
        found: set[UUID] = set()
        value: object
        for value in self._session.scalars(statement):
            if isinstance(value, UUID):
                found.add(value)
        return found

    def _modes(self, asset_ids: list[UUID]) -> dict[UUID, tuple[str, int]]:
        statement = select(EntityConfigurationRow).where(
            EntityConfigurationRow.asset_id.in_(asset_ids),
        )
        found: dict[UUID, tuple[str, int]] = {}
        row: EntityConfigurationRow
        for row in self._session.scalars(statement):
            if row.asset_id is None:
                continue
            found[row.asset_id] = (row.refresh_mode, row.interval_seconds)
        return found


def _build_source() -> Select[tuple[UUID, datetime, str]]:
    """Asset builds plus table builds, both keyed by asset id."""
    asset_jobs: Select[tuple[UUID, datetime, str]] = select(
        CatalogJobRow.subject_id.label("asset_id"),
        CatalogJobRow.created_at.label("built_at"),
        CatalogJobRow.status.label("status"),
    ).where(
        CatalogJobRow.kind == CatalogJobKind.ASSET.value,
        CatalogJobRow.lane == "",
    )
    table_jobs: Select[tuple[UUID, datetime, str]] = (
        select(
            CatalogTableRow.asset_id.label("asset_id"),
            CatalogJobRow.created_at.label("built_at"),
            CatalogJobRow.status.label("status"),
        )
        .join(CatalogTableRow, CatalogTableRow.id == CatalogJobRow.subject_id)
        .where(
            CatalogJobRow.kind == CatalogJobKind.KNOWLEDGE.value,
            CatalogJobRow.lane == "",
            CatalogTableRow.asset_id.is_not(None),
        )
    )
    return union_all(asset_jobs, table_jobs)


def _build_times() -> Subquery:
    source = _build_source().subquery()
    return (
        select(
            source.c.asset_id.label("asset_id"),
            func.max(source.c.built_at).label("last_built_at"),
        )
        .where(source.c.status == CatalogJobStatus.SUCCEEDED.value)
        .group_by(source.c.asset_id)
        .subquery()
    )


def _membership_counts() -> Subquery:
    return (
        select(
            AssetMembershipRow.asset_id.label("asset_id"),
            func.count().label("membership_count"),
        )
        .group_by(AssetMembershipRow.asset_id)
        .subquery()
    )


def _refresh_modes() -> Subquery:
    return (
        select(
            EntityConfigurationRow.asset_id.label("asset_id"),
            EntityConfigurationRow.refresh_mode.label("refresh_mode"),
            EntityConfigurationRow.interval_seconds.label("interval_seconds"),
        )
        .where(EntityConfigurationRow.asset_id.is_not(None))
        .subquery()
    )


def _freshness_score(
    last_built: ColumnElement[datetime | None],
    interval_seconds: ColumnElement[int | None],
    refresh_mode: ColumnElement[str | None],
    dialect: str,
) -> ColumnElement[int]:
    age: ColumnElement[float] = case(
        (last_built.is_(None), None),
        else_=_age_seconds(last_built, dialect),
    )
    interval: ColumnElement[int] = func.coalesce(interval_seconds, _DEFAULT_INTERVAL)
    elapsed: ColumnElement[float] = age / interval * 100
    raw: ColumnElement[float] = 100 - elapsed
    clamped: ColumnElement[int] = case(
        (raw < 0, 0),
        (raw > 100, 100),
        else_=raw,
    )
    return case(
        (last_built.is_(None), 0),
        (refresh_mode == AssetRefreshMode.MANUAL.value, 100),
        else_=clamped,
    )


def _age_seconds(
    last_built: ColumnElement[datetime | None],
    dialect: str,
) -> ColumnElement[float]:
    if dialect == "postgresql":
        return func.extract("epoch", func.now() - last_built)
    return (func.julianday("now") - func.julianday(last_built)) * 86400.0


def _context_state_expr() -> ColumnElement[str]:
    return case(
        (AssetRow.context.is_(None), "none"),
        (AssetRow.context_origin == "USER", "user"),
        else_="llm",
    )


def _state(row: AssetRow) -> ContextState:
    if row.context is None or row.context.strip() == "":
        return "none"
    if row.context_origin == "USER":
        return "user"
    return "llm"


def _score(mode: str, interval: int, last_built: datetime | None, now: datetime) -> int:
    if last_built is None:
        return 0
    if mode == AssetRefreshMode.MANUAL.value:
        return 100
    span: int = interval if interval > 0 else _DEFAULT_INTERVAL
    age: float = (_aware(now) - _aware(last_built)).total_seconds()
    raw: int = 100 - int(age / span * 100)
    return max(0, min(100, raw))


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


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
