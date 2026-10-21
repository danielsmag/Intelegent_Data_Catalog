"""Generic assets, memberships, and backfill from tables, DAGs, and files.

Revision 0001 creates the current models on a fresh database. This revision
adds the same tables when the database was created earlier, then copies
existing tables, DAGs, DAG attachments, and workspace files into assets.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection, CursorResult

from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_SLUG

revision: str = "20261021_0019"
down_revision: str | None = "20261016_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_CHECK: str = "kind IN ('TABLE', 'AIRFLOW_DAG', 'NOTEBOOK', 'FILE')"
_STATUS_CHECK: str = "status IN ('ACTIVE', 'MISSING', 'DEPRECATED')"
_ORIGIN_CHECK: str = "context_origin IN ('USER', 'LLM')"
_ONE_MEMBERSHIP: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL) OR (space_id IS NULL AND scope_id IS NOT NULL)"
)
_ONE_CONFIGURATION: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL AND catalog_table_id IS NULL "
    "AND asset_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NOT NULL AND catalog_table_id IS NULL "
    "AND asset_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NULL AND catalog_table_id IS NOT NULL "
    "AND asset_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NULL AND catalog_table_id IS NULL "
    "AND asset_id IS NOT NULL)"
)
_ONE_CONFIGURATION_LEGACY: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL AND catalog_table_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NOT NULL AND catalog_table_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NULL AND catalog_table_id IS NOT NULL)"
)
_REFRESH_CHECK: str = "refresh_mode IN ('INTERVAL', 'ON_CHANGE', 'MANUAL')"
_JOB_CONSTRAINT: str = "ck_catalog_jobs_kind"
_JOB_WITH_ASSET: str = "kind IN ('knowledge', 'scope', 'space', 'context', 'airflow', 'asset')"
_JOB_WITHOUT_ASSET: str = "kind IN ('knowledge', 'scope', 'space', 'context', 'airflow')"
_CONFIG_CONSTRAINT: str = "ck_entity_configurations_one_subject"
_REFRESH_CONSTRAINT: str = "ck_entity_configurations_refresh_mode"


def upgrade() -> None:
    """Add asset storage and copy existing catalog objects into it."""
    bind: Connection = op.get_bind()
    _create_assets(bind)
    _create_memberships(bind)
    _create_contexts(bind)
    _add_asset_column(bind, "catalog_tables", "fk_catalog_tables_asset", "uq_catalog_tables_asset")
    _add_asset_column(bind, "airflow_dags", "fk_airflow_dags_asset", "uq_airflow_dags_asset")
    _add_asset_column(
        bind,
        "workspace_nodes",
        "fk_workspace_nodes_asset",
        "uq_workspace_nodes_asset",
    )
    _add_configuration_columns(bind)
    if not _check_contains(bind, "catalog_jobs", _JOB_CONSTRAINT, "'asset'"):
        _replace_check("catalog_jobs", _JOB_CONSTRAINT, _JOB_WITH_ASSET)
    if not _check_contains(bind, "entity_configurations", _CONFIG_CONSTRAINT, "asset_id"):
        _replace_check("entity_configurations", _CONFIG_CONSTRAINT, _ONE_CONFIGURATION)
    _backfill_tables(bind)
    _backfill_dags(bind)
    _backfill_files(bind)
    _move_dag_attachments(bind)
    _move_table_configurations(bind)
    _relax_scope_id(bind)


def downgrade() -> None:
    """Drop asset storage and restore the previous configuration check."""
    bind: Connection = op.get_bind()
    _restore_dag_attachments(bind)
    _restore_table_configurations(bind)
    _drop_asset_column(
        bind,
        "workspace_nodes",
        "fk_workspace_nodes_asset",
        "uq_workspace_nodes_asset",
    )
    _drop_asset_column(bind, "airflow_dags", "fk_airflow_dags_asset", "uq_airflow_dags_asset")
    _drop_asset_column(bind, "catalog_tables", "fk_catalog_tables_asset", "uq_catalog_tables_asset")
    _tighten_scope_id(bind)
    _drop_configuration_columns(bind)
    inspector: Inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "asset_knowledge_contexts" in tables:
        op.drop_table("asset_knowledge_contexts")
    if "asset_memberships" in tables:
        op.drop_table("asset_memberships")
    if "assets" in tables:
        op.drop_table("assets")
    if _check_contains(bind, "catalog_jobs", _JOB_CONSTRAINT, "'asset'"):
        _replace_check("catalog_jobs", _JOB_CONSTRAINT, _JOB_WITHOUT_ASSET)


def _create_assets(bind: Connection) -> None:
    if "assets" in _tables(bind):
        return
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("context_origin", sa.String(length=32), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_KIND_CHECK, name="ck_assets_kind"),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_assets_status"),
        sa.CheckConstraint(_ORIGIN_CHECK, name="ck_assets_context_origin"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    _partial_index(
        bind,
        "uq_assets_owner_slug",
        "assets",
        ["workspace_id", "owner_user_id", "slug"],
        "owner_user_id IS NOT NULL",
    )
    _partial_index(
        bind,
        "uq_assets_shared_slug",
        "assets",
        ["workspace_id", "slug"],
        "owner_user_id IS NULL",
    )
    op.create_index("ix_assets_kind", "assets", ["kind"])
    op.create_index("ix_assets_workspace_owner", "assets", ["workspace_id", "owner_user_id"])
    op.create_index("ix_assets_updated_at", "assets", ["updated_at"])


def _create_memberships(bind: Connection) -> None:
    if "asset_memberships" in _tables(bind):
        return
    op.create_table(
        "asset_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=True),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ONE_MEMBERSHIP, name="ck_asset_memberships_one_target"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_memberships_asset", "asset_memberships", ["asset_id"])
    op.create_index("ix_asset_memberships_scope", "asset_memberships", ["scope_id"])
    op.create_index("ix_asset_memberships_space", "asset_memberships", ["space_id"])
    _partial_index(
        bind,
        "uq_asset_memberships_scope",
        "asset_memberships",
        ["asset_id", "scope_id"],
        "scope_id IS NOT NULL",
    )
    _partial_index(
        bind,
        "uq_asset_memberships_space",
        "asset_memberships",
        ["asset_id", "space_id"],
        "space_id IS NOT NULL",
    )


def _create_contexts(bind: Connection) -> None:
    if "asset_knowledge_contexts" in _tables(bind):
        return
    op.create_table(
        "asset_knowledge_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", name="uq_asset_knowledge_contexts_asset"),
    )


def _add_asset_column(bind: Connection, table: str, fk_name: str, index_name: str) -> None:
    if table not in _tables(bind):
        return
    present: set[str] = _column_names(bind, table)
    if "asset_id" not in present:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("asset_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(fk_name, "assets", ["asset_id"], ["id"], ondelete="SET NULL")
    if index_name not in _index_names(bind, table):
        _partial_index(bind, index_name, table, ["asset_id"], "asset_id IS NOT NULL")


def _drop_asset_column(bind: Connection, table: str, fk_name: str, index_name: str) -> None:
    if table not in _tables(bind):
        return
    if index_name in _index_names(bind, table):
        op.drop_index(index_name, table_name=table)
    present: set[str] = _column_names(bind, table)
    fks: set[str] = _fk_names(bind, table)
    if "asset_id" not in present and fk_name not in fks:
        return
    with op.batch_alter_table(table) as batch:
        if fk_name in fks:
            batch.drop_constraint(fk_name, type_="foreignkey")
        if "asset_id" in present:
            batch.drop_column("asset_id")


def _add_configuration_columns(bind: Connection) -> None:
    if "entity_configurations" not in _tables(bind):
        return
    present: set[str] = _column_names(bind, "entity_configurations")
    uniques: set[str] = _unique_names(bind, "entity_configurations")
    checks: set[str] = _check_names(bind, "entity_configurations")
    needs_asset: bool = "asset_id" not in present
    needs_unique: bool = "uq_entity_configurations_asset" not in uniques
    needs_mode: bool = "refresh_mode" not in present
    needs_check: bool = _REFRESH_CONSTRAINT not in checks
    if not needs_asset and not needs_unique and not needs_mode and not needs_check:
        return
    with op.batch_alter_table("entity_configurations") as batch:
        if needs_asset:
            batch.add_column(sa.Column("asset_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_entity_configurations_asset",
                "assets",
                ["asset_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if needs_unique:
            batch.create_unique_constraint("uq_entity_configurations_asset", ["asset_id"])
        if needs_mode:
            batch.add_column(
                sa.Column(
                    "refresh_mode",
                    sa.String(length=16),
                    nullable=False,
                    server_default=sa.text("'INTERVAL'"),
                )
            )
        if needs_check:
            batch.create_check_constraint(_REFRESH_CONSTRAINT, _REFRESH_CHECK)


def _drop_configuration_columns(bind: Connection) -> None:
    if "entity_configurations" not in _tables(bind):
        return
    present: set[str] = _column_names(bind, "entity_configurations")
    checks: set[str] = _check_names(bind, "entity_configurations")
    uniques: set[str] = _unique_names(bind, "entity_configurations")
    fks: set[str] = _fk_names(bind, "entity_configurations")
    if _check_contains(bind, "entity_configurations", _CONFIG_CONSTRAINT, "asset_id"):
        _replace_check("entity_configurations", _CONFIG_CONSTRAINT, _ONE_CONFIGURATION_LEGACY)
    with op.batch_alter_table("entity_configurations") as batch:
        if _REFRESH_CONSTRAINT in checks:
            batch.drop_constraint(_REFRESH_CONSTRAINT, type_="check")
        if "uq_entity_configurations_asset" in uniques:
            batch.drop_constraint("uq_entity_configurations_asset", type_="unique")
        if "fk_entity_configurations_asset" in fks:
            batch.drop_constraint("fk_entity_configurations_asset", type_="foreignkey")
        if "refresh_mode" in present:
            batch.drop_column("refresh_mode")
        if "asset_id" in present:
            batch.drop_column("asset_id")


def _backfill_tables(bind: Connection) -> None:
    if "catalog_tables" not in _tables(bind):
        return
    if "asset_id" not in _column_names(bind, "catalog_tables"):
        return
    workspace_id: uuid.UUID | None = _shared_workspace_id(bind)
    if workspace_id is None:
        return
    used: set[tuple[str, str]] = _used_slugs(bind)
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT id, name, description, context, context_origin, scope_id, status, "
            "source_key, table_format, last_synced_at, created_at, updated_at "
            "FROM catalog_tables WHERE asset_id IS NULL"
        )
    )
    record: tuple[object, ...]
    for record in rows:
        asset_id: uuid.UUID = uuid.uuid4()
        table_id: uuid.UUID = _as_uuid(record[0])
        scope_id: uuid.UUID | None = _as_uuid(record[5]) if record[5] is not None else None
        name: str = _clip(str(record[1]), 255)
        slug: str = _claim_slug(used, workspace_id, None, str(record[7]) or name)
        raw_status: str = str(record[6])
        status: str = raw_status if raw_status in {"ACTIVE", "MISSING", "DEPRECATED"} else "ACTIVE"
        created_at: datetime = _moment(record[10])
        _insert_asset(
            bind,
            asset_id=asset_id,
            kind="TABLE",
            name=name,
            slug=slug,
            description=_optional_text(record[2]),
            context=_optional_text(record[3]),
            context_origin=_optional_text(record[4]),
            workspace_id=workspace_id,
            owner_user_id=None,
            created_by=None,
            properties={"source_key": str(record[7]), "table_format": record[8]},
            status=status,
            last_synced_at=_optional_moment(record[9]),
            created_at=created_at,
            updated_at=_moment(record[11]),
        )
        _set_asset_id(bind, "catalog_tables", table_id, asset_id)
        if scope_id is not None:
            _insert_membership(bind, asset_id, None, scope_id, created_at)


def _backfill_dags(bind: Connection) -> None:
    if "airflow_dags" not in _tables(bind) or "asset_id" not in _column_names(bind, "airflow_dags"):
        return
    workspace_id: uuid.UUID | None = _shared_workspace_id(bind)
    if workspace_id is None:
        return
    used: set[tuple[str, str]] = _used_slugs(bind)
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT id, instance_id, dag_id, display_name, description, is_stale, last_seen_at "
            "FROM airflow_dags WHERE asset_id IS NULL"
        )
    )
    record: tuple[object, ...]
    for record in rows:
        asset_id: uuid.UUID = uuid.uuid4()
        dag_row_id: uuid.UUID = _as_uuid(record[0])
        instance_id: uuid.UUID = _as_uuid(record[1])
        dag_id: str = str(record[2])
        label: str = dag_id if record[3] is None else str(record[3])
        seen_at: datetime = _moment(record[6])
        _insert_asset(
            bind,
            asset_id=asset_id,
            kind="AIRFLOW_DAG",
            name=_clip(label, 255),
            slug=_claim_slug(used, workspace_id, None, f"{instance_id}-{dag_id}"),
            description=_optional_text(record[4]),
            context=None,
            context_origin=None,
            workspace_id=workspace_id,
            owner_user_id=None,
            created_by=None,
            properties={"dag_id": dag_id, "instance_id": str(instance_id)},
            status="MISSING" if bool(record[5]) else "ACTIVE",
            last_synced_at=seen_at,
            created_at=seen_at,
            updated_at=seen_at,
        )
        _set_asset_id(bind, "airflow_dags", dag_row_id, asset_id)


def _backfill_files(bind: Connection) -> None:
    if "workspace_nodes" not in _tables(bind):
        return
    if "asset_id" not in _column_names(bind, "workspace_nodes"):
        return
    used: set[tuple[str, str]] = _used_slugs(bind)
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT id, workspace_id, owner_user_id, created_by, name, path, "
            "content_type, created_at, updated_at "
            "FROM workspace_nodes WHERE kind = 'FILE' AND asset_id IS NULL"
        )
    )
    record: tuple[object, ...]
    for record in rows:
        asset_id: uuid.UUID = uuid.uuid4()
        node_id: uuid.UUID = _as_uuid(record[0])
        workspace_id: uuid.UUID = _as_uuid(record[1])
        owner_id: uuid.UUID | None = _as_uuid(record[2]) if record[2] is not None else None
        created_by: uuid.UUID | None = _as_uuid(record[3]) if record[3] is not None else None
        name: str = str(record[4])
        kind: str = "NOTEBOOK" if name.lower().endswith(".ipynb") else "FILE"
        created_at: datetime = _moment(record[7])
        _insert_asset(
            bind,
            asset_id=asset_id,
            kind=kind,
            name=_clip(name, 255),
            slug=_claim_slug(used, workspace_id, owner_id, str(record[5]) or name),
            description=None,
            context=None,
            context_origin=None,
            workspace_id=workspace_id,
            owner_user_id=owner_id,
            created_by=created_by,
            properties={"content_type": record[6], "path": str(record[5]), "node_id": str(node_id)},
            status="ACTIVE",
            last_synced_at=None,
            created_at=created_at,
            updated_at=_moment(record[8]),
        )
        _set_asset_id(bind, "workspace_nodes", node_id, asset_id)


def _move_dag_attachments(bind: Connection) -> None:
    if "airflow_attachments" not in _tables(bind) or "airflow_dags" not in _tables(bind):
        return
    if "asset_id" not in _column_names(bind, "airflow_dags"):
        return
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT d.asset_id, a.space_id, a.scope_id, a.created_at "
            "FROM airflow_attachments AS a "
            "JOIN airflow_dags AS d ON d.id = a.dag_row_id "
            "WHERE a.dag_row_id IS NOT NULL AND d.asset_id IS NOT NULL"
        )
    )
    record: tuple[object, ...]
    for record in rows:
        asset_id: uuid.UUID = _as_uuid(record[0])
        space_id: uuid.UUID | None = _as_uuid(record[1]) if record[1] is not None else None
        scope_id: uuid.UUID | None = _as_uuid(record[2]) if record[2] is not None else None
        _insert_membership(bind, asset_id, space_id, scope_id, _moment(record[3]))
    bind.execute(sa.text("DELETE FROM airflow_attachments WHERE dag_row_id IS NOT NULL"))


def _move_table_configurations(bind: Connection) -> None:
    if "entity_configurations" not in _tables(bind):
        return
    if "asset_id" not in _column_names(bind, "entity_configurations"):
        return
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT c.id, t.asset_id FROM entity_configurations AS c "
            "JOIN catalog_tables AS t ON t.id = c.catalog_table_id "
            "WHERE c.catalog_table_id IS NOT NULL AND c.asset_id IS NULL "
            "AND t.asset_id IS NOT NULL"
        )
    )
    record: tuple[object, ...]
    for record in rows:
        bind.execute(
            sa.text(
                "UPDATE entity_configurations SET asset_id = :asset_id, catalog_table_id = NULL "
                "WHERE id = :id"
            ).bindparams(
                sa.bindparam("asset_id", type_=sa.Uuid()),
                sa.bindparam("id", type_=sa.Uuid()),
            ),
            {"asset_id": _as_uuid(record[1]), "id": _as_uuid(record[0])},
        )


def _restore_table_configurations(bind: Connection) -> None:
    if "entity_configurations" not in _tables(bind) or "catalog_tables" not in _tables(bind):
        return
    if "asset_id" not in _column_names(bind, "entity_configurations"):
        return
    bind.execute(
        sa.text(
            "DELETE FROM entity_configurations WHERE asset_id IS NOT NULL AND asset_id NOT IN ("
            "SELECT asset_id FROM catalog_tables WHERE asset_id IS NOT NULL)"
        )
    )
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT c.id, t.id FROM entity_configurations AS c "
            "JOIN catalog_tables AS t ON t.asset_id = c.asset_id "
            "WHERE c.asset_id IS NOT NULL AND c.catalog_table_id IS NULL"
        )
    )
    record: tuple[object, ...]
    for record in rows:
        bind.execute(
            sa.text(
                "UPDATE entity_configurations SET catalog_table_id = :table_id, asset_id = NULL "
                "WHERE id = :id"
            ).bindparams(
                sa.bindparam("table_id", type_=sa.Uuid()),
                sa.bindparam("id", type_=sa.Uuid()),
            ),
            {"table_id": _as_uuid(record[1]), "id": _as_uuid(record[0])},
        )


def _restore_dag_attachments(bind: Connection) -> None:
    if "airflow_attachments" not in _tables(bind) or "airflow_dags" not in _tables(bind):
        return
    if "asset_memberships" not in _tables(bind):
        return
    if "asset_id" not in _column_names(bind, "airflow_dags"):
        return
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT d.id, m.space_id, m.scope_id, m.created_at "
            "FROM asset_memberships AS m "
            "JOIN airflow_dags AS d ON d.asset_id = m.asset_id"
        )
    )
    record: tuple[object, ...]
    for record in rows:
        bind.execute(
            sa.text(
                "INSERT INTO airflow_attachments "
                "(id, airflow_instance_id, dag_row_id, space_id, scope_id, created_at) "
                "VALUES (:id, NULL, :dag_row_id, :space_id, :scope_id, :created_at)"
            ).bindparams(
                sa.bindparam("id", type_=sa.Uuid()),
                sa.bindparam("dag_row_id", type_=sa.Uuid()),
                sa.bindparam("space_id", type_=sa.Uuid()),
                sa.bindparam("scope_id", type_=sa.Uuid()),
                sa.bindparam("created_at", type_=sa.DateTime(timezone=True)),
            ),
            {
                "id": uuid.uuid4(),
                "dag_row_id": _as_uuid(record[0]),
                "space_id": _as_uuid(record[1]) if record[1] is not None else None,
                "scope_id": _as_uuid(record[2]) if record[2] is not None else None,
                "created_at": _moment(record[3]),
            },
        )


def _relax_scope_id(bind: Connection) -> None:
    if "catalog_tables" not in _tables(bind):
        return
    column: dict[str, object] | None = _column(bind, "catalog_tables", "scope_id")
    if column is None or bool(column.get("nullable", True)):
        return
    with op.batch_alter_table("catalog_tables") as batch:
        batch.alter_column("scope_id", existing_type=sa.Uuid(), nullable=True)


def _tighten_scope_id(bind: Connection) -> None:
    if "catalog_tables" not in _tables(bind):
        return
    column: dict[str, object] | None = _column(bind, "catalog_tables", "scope_id")
    if column is None or not bool(column.get("nullable", True)):
        return
    missing: object = bind.execute(
        sa.text("SELECT COUNT(*) FROM catalog_tables WHERE scope_id IS NULL")
    ).scalar()
    if int(missing or 0) > 0:
        return
    with op.batch_alter_table("catalog_tables") as batch:
        batch.alter_column("scope_id", existing_type=sa.Uuid(), nullable=False)


def _insert_asset(
    bind: Connection,
    *,
    asset_id: uuid.UUID,
    kind: str,
    name: str,
    slug: str,
    description: str | None,
    context: str | None,
    context_origin: str | None,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID | None,
    created_by: uuid.UUID | None,
    properties: dict[str, object],
    status: str,
    last_synced_at: datetime | None,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    bind.execute(
        sa.text(
            "INSERT INTO assets ("
            "id, kind, name, slug, description, context, context_origin, workspace_id, "
            "owner_user_id, created_by, properties, status, last_synced_at, created_at, updated_at"
            ") VALUES ("
            ":id, :kind, :name, :slug, :description, :context, :context_origin, :workspace_id, "
            ":owner_user_id, :created_by, :properties, :status, :last_synced_at, :created_at, "
            ":updated_at)"
        ).bindparams(
            sa.bindparam("id", type_=sa.Uuid()),
            sa.bindparam("workspace_id", type_=sa.Uuid()),
            sa.bindparam("owner_user_id", type_=sa.Uuid()),
            sa.bindparam("created_by", type_=sa.Uuid()),
            sa.bindparam("properties", type_=sa.JSON()),
            sa.bindparam("last_synced_at", type_=sa.DateTime(timezone=True)),
            sa.bindparam("created_at", type_=sa.DateTime(timezone=True)),
            sa.bindparam("updated_at", type_=sa.DateTime(timezone=True)),
        ),
        {
            "id": asset_id,
            "kind": kind,
            "name": name,
            "slug": slug,
            "description": description,
            "context": context,
            "context_origin": context_origin,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "created_by": created_by,
            "properties": properties,
            "status": status,
            "last_synced_at": last_synced_at,
            "created_at": created_at,
            "updated_at": updated_at,
        },
    )


def _insert_membership(
    bind: Connection,
    asset_id: uuid.UUID,
    space_id: uuid.UUID | None,
    scope_id: uuid.UUID | None,
    created_at: datetime,
) -> None:
    if space_id is None and scope_id is None:
        return
    if scope_id is not None:
        existing: object = bind.execute(
            sa.text(
                "SELECT id FROM asset_memberships WHERE asset_id = :asset_id "
                "AND scope_id = :scope_id"
            ).bindparams(
                sa.bindparam("asset_id", type_=sa.Uuid()),
                sa.bindparam("scope_id", type_=sa.Uuid()),
            ),
            {"asset_id": asset_id, "scope_id": scope_id},
        ).scalar()
    else:
        existing = bind.execute(
            sa.text(
                "SELECT id FROM asset_memberships WHERE asset_id = :asset_id "
                "AND space_id = :space_id"
            ).bindparams(
                sa.bindparam("asset_id", type_=sa.Uuid()),
                sa.bindparam("space_id", type_=sa.Uuid()),
            ),
            {"asset_id": asset_id, "space_id": space_id},
        ).scalar()
    if existing is not None:
        return
    bind.execute(
        sa.text(
            "INSERT INTO asset_memberships (id, asset_id, space_id, scope_id, created_at) "
            "VALUES (:id, :asset_id, :space_id, :scope_id, :created_at)"
        ).bindparams(
            sa.bindparam("id", type_=sa.Uuid()),
            sa.bindparam("asset_id", type_=sa.Uuid()),
            sa.bindparam("space_id", type_=sa.Uuid()),
            sa.bindparam("scope_id", type_=sa.Uuid()),
            sa.bindparam("created_at", type_=sa.DateTime(timezone=True)),
        ),
        {
            "id": uuid.uuid4(),
            "asset_id": asset_id,
            "space_id": space_id,
            "scope_id": scope_id,
            "created_at": created_at,
        },
    )


def _set_asset_id(bind: Connection, table: str, row_id: uuid.UUID, asset_id: uuid.UUID) -> None:
    allowed: dict[str, str] = {
        "catalog_tables": "UPDATE catalog_tables SET asset_id = :asset_id WHERE id = :id",
        "airflow_dags": "UPDATE airflow_dags SET asset_id = :asset_id WHERE id = :id",
        "workspace_nodes": "UPDATE workspace_nodes SET asset_id = :asset_id WHERE id = :id",
    }
    sql: str | None = allowed.get(table)
    if sql is None:
        raise ValueError(f"unknown asset link table: {table}")
    bind.execute(
        sa.text(sql).bindparams(
            sa.bindparam("asset_id", type_=sa.Uuid()),
            sa.bindparam("id", type_=sa.Uuid()),
        ),
        {"asset_id": asset_id, "id": row_id},
    )


def _used_slugs(bind: Connection) -> set[tuple[str, str]]:
    if "assets" not in _tables(bind):
        return set()
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text("SELECT workspace_id, owner_user_id, slug FROM assets")
    )
    used: set[tuple[str, str]] = set()
    record: tuple[object, ...]
    for record in rows:
        owner: str = "" if record[1] is None else str(_as_uuid(record[1]))
        used.add((f"{_as_uuid(record[0])}:{owner}", str(record[2])))
    return used


def _claim_slug(
    used: set[tuple[str, str]],
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID | None,
    raw: str,
) -> str:
    owner: str = "" if owner_user_id is None else str(owner_user_id)
    bucket: str = f"{workspace_id}:{owner}"
    cleaned: str = "".join(char.lower() if char.isalnum() else "-" for char in raw).strip("-")
    if cleaned == "":
        cleaned = "asset"
    cleaned = cleaned[:200]
    candidate: str = cleaned
    number: int = 2
    while (bucket, candidate) in used:
        suffix: str = f"-{number}"
        candidate = f"{cleaned[: 200 - len(suffix)]}{suffix}"
        number += 1
    used.add((bucket, candidate))
    return candidate


def _shared_workspace_id(bind: Connection) -> uuid.UUID | None:
    if "workspaces" not in _tables(bind):
        return None
    found: object = bind.execute(
        sa.text("SELECT id FROM workspaces WHERE slug = :slug"),
        {"slug": SHARED_WORKSPACE_SLUG},
    ).scalar()
    if found is None:
        return None
    return _as_uuid(found)


def _partial_index(
    bind: Connection,
    name: str,
    table: str,
    columns: list[str],
    predicate: str,
) -> None:
    if name in _index_names(bind, table):
        return
    op.create_index(
        name,
        table,
        columns,
        unique=True,
        sqlite_where=sa.text(predicate),
        postgresql_where=sa.text(predicate),
    )


def _tables(bind: Connection) -> set[str]:
    inspector: Inspector = inspect(bind)
    return set(inspector.get_table_names())


def _column_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    return {str(column["name"]) for column in inspector.get_columns(table)}


def _column(bind: Connection, table: str, name: str) -> dict[str, object] | None:
    inspector: Inspector = inspect(bind)
    column: dict[str, object]
    for column in inspector.get_columns(table):
        if column.get("name") == name:
            return column
    return None


def _index_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    found: set[str] = set()
    item: dict[str, object]
    for item in inspector.get_indexes(table):
        label: object = item.get("name")
        if isinstance(label, str):
            found.add(label)
    return found


def _unique_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    found: set[str] = set()
    item: dict[str, object]
    for item in inspector.get_unique_constraints(table):
        label: object = item.get("name")
        if isinstance(label, str):
            found.add(label)
    return found


def _fk_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    found: set[str] = set()
    item: dict[str, object]
    for item in inspector.get_foreign_keys(table):
        label: object = item.get("name")
        if isinstance(label, str):
            found.add(label)
    return found


def _check_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    found: set[str] = set()
    item: dict[str, object]
    for item in inspector.get_check_constraints(table):
        label: object = item.get("name")
        if isinstance(label, str):
            found.add(label)
    return found


def _check_contains(bind: Connection, table: str, name: str, token: str) -> bool:
    if table not in _tables(bind):
        return False
    inspector: Inspector = inspect(bind)
    check: dict[str, object]
    for check in inspector.get_check_constraints(table):
        if check.get("name") != name:
            continue
        sqltext: object = check.get("sqltext")
        return isinstance(sqltext, str) and token in sqltext
    return False


def _replace_check(table: str, name: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def _as_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _moment(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("timestamp missing")


def _optional_moment(value: object) -> datetime | None:
    if value is None:
        return None
    return _moment(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]
