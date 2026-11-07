"""Deterministic assets, separate from agents.

Revision 0001 creates the current models on a fresh database. This revision
adds the same tables when the database was created earlier, and drops the
agent operator schedule columns agents no longer use.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection


class _Batch(Protocol):
    """The slice of Alembic's batch operation this migration calls."""

    def drop_constraint(self, name: str, *, type_: str) -> None:
        """Drop one constraint."""

    def drop_index(self, name: str) -> None:
        """Drop one secondary index."""

    def drop_column(self, name: str) -> None:
        """Drop one column."""


revision: str = "20261108_0027"
down_revision: str | None = "20261107_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ASSET_KIND: str = (
    "kind IN ('AIRFLOW_INSTANCE', 'ICEBERG_TABLE', 'ICEBERG_SCHEMA', 'GITHUB_REPO')"
)
_ASSET_STATUS: str = "status IN ('ACTIVE', 'MISSING')"
_FILE_STATUS: str = "status IN ('OK', 'ERROR', 'REMOVED')"
_ITEM_STATUS: str = "status IN ('APPLIED', 'UNRESOLVED', 'ERROR', 'REMOVED')"
_RUN_STATUS: str = "status IN ('queued', 'running', 'succeeded', 'failed')"
_ITEM_RUN: str = (
    "run_status IS NULL OR run_status IN ('queued', 'running', 'succeeded', 'failed')"
)
_TRIGGER: str = "trigger IN ('apply', 'schedule', 'manual')"
_TASK_STATE: str = (
    "state IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'upstream_failed')"
)
_AGENT_KIND: str = (
    "kind IN ('TABLE', 'AIRFLOW_DAG', 'AIRFLOW_INSTANCE', 'NOTEBOOK', 'FILE', 'SCHEMA')"
)
_AGENT_STATUS: str = "status IN ('ACTIVE', 'MISSING', 'DEPRECATED')"
_AGENT_ITEM_COLUMNS: tuple[str, ...] = (
    "run_status",
    "run_error",
    "ran_at",
    "schedule",
    "next_run_at",
)
# Table rename does not rename indexes. These names still sit on the agent
# tables and block the new asset tables, whose indexes reuse them.
_RELEASED_INDEXES: tuple[tuple[str, str], ...] = (
    ("assets_pkey", "agents_pkey"),
    ("asset_definition_files_pkey", "agent_definition_files_pkey"),
    ("uq_asset_definition_files_key", "uq_agent_definition_files_key"),
    ("ix_asset_definition_files_status", "ix_agent_definition_files_status"),
    ("asset_definition_items_pkey", "agent_definition_items_pkey"),
    ("uq_asset_definition_items_file_key", "uq_agent_definition_items_file_key"),
    ("ix_asset_definition_items_status", "ix_agent_definition_items_status"),
    ("ix_asset_definition_items_next_run", "ix_agent_definition_items_next_run"),
)


def upgrade() -> None:
    """Add deterministic asset tables and drop unused agent run columns."""
    bind: Connection = op.get_bind()
    _release_indexes(bind)
    _free_agent_constraint(bind, "ck_assets_kind", "ck_agents_kind", _AGENT_KIND)
    _free_agent_constraint(bind, "ck_assets_status", "ck_agents_status", _AGENT_STATUS)
    _create_missing(bind)
    _drop_columns(bind, "agent_definition_items", _AGENT_ITEM_COLUMNS)
    _drop_columns(bind, "agent_operator_runs", ("item_id",))


def downgrade() -> None:
    """Drop deterministic asset tables and restore agent run columns."""
    bind: Connection = op.get_bind()
    tables: set[str] = _tables(bind)
    name: str
    for name in (
        "asset_outputs",
        "asset_run_tasks",
        "asset_runs",
        "asset_definition_items",
        "asset_definition_files",
        "assets",
    ):
        if name in tables:
            op.drop_table(name)
    _restore_agent_columns(bind)


def _create_missing(bind: Connection) -> None:
    tables: set[str] = _tables(bind)
    if "assets" not in tables:
        _create_assets()
    if "asset_definition_files" not in tables:
        _create_files()
    if "asset_definition_items" not in tables:
        _create_items()
    if "asset_runs" not in tables:
        _create_runs()
    if "asset_run_tasks" not in tables:
        _create_tasks()
    if "asset_outputs" not in tables:
        _create_outputs()


def _create_assets() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ASSET_KIND, name="ck_assets_kind"),
        sa.CheckConstraint(_ASSET_STATUS, name="ck_assets_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_assets_key"),
    )
    op.create_index("ix_assets_kind", "assets", ["kind"])
    op.create_index("ix_assets_status", "assets", ["status"])


def _create_files() -> None:
    op.create_table(
        "asset_definition_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("folder_kind", sa.String(length=16), nullable=False),
        sa.Column("etag", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("last_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("folder_kind IN ('ASSET')", name="ck_asset_definition_files_kind"),
        sa.CheckConstraint(_FILE_STATUS, name="ck_asset_definition_files_status"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_asset_definition_files_size"),
        sa.CheckConstraint("item_count >= 0", name="ck_asset_definition_files_items"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_asset_definition_files_key"),
    )
    op.create_index("ix_asset_definition_files_status", "asset_definition_files", ["status"])


def _create_items() -> None:
    op.create_table(
        "asset_definition_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("item_key", sa.String(length=512), nullable=False),
        sa.Column("declaration", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("run_status", sa.String(length=16), nullable=True),
        sa.Column("run_error", sa.Text(), nullable=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schedule", sa.String(length=128), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('ASSET')", name="ck_asset_definition_items_kind"),
        sa.CheckConstraint(_ITEM_STATUS, name="ck_asset_definition_items_status"),
        sa.CheckConstraint(_ITEM_RUN, name="ck_asset_definition_items_run_status"),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["asset_definition_files.id"],
            name="fk_asset_definition_items_file",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_asset_definition_items_asset",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id", "item_key", name="uq_asset_definition_items_file_key"),
    )
    op.create_index("ix_asset_definition_items_status", "asset_definition_items", ["status"])
    op.create_index("ix_asset_definition_items_asset", "asset_definition_items", ["asset_id"])
    op.create_index(
        "ix_asset_definition_items_next_run",
        "asset_definition_items",
        ["next_run_at"],
        sqlite_where=sa.text("next_run_at IS NOT NULL"),
        postgresql_where=sa.text("next_run_at IS NOT NULL"),
    )


def _create_runs() -> None:
    op.create_table(
        "asset_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("force", sa.Boolean(), nullable=False),
        sa.Column("try_number", sa.Integer(), nullable=False),
        sa.Column("operator_params", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_RUN_STATUS, name="ck_asset_runs_status"),
        sa.CheckConstraint(_TRIGGER, name="ck_asset_runs_trigger"),
        sa.CheckConstraint("try_number >= 1", name="ck_asset_runs_try"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_asset_runs_asset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["asset_definition_items.id"],
            name="fk_asset_runs_item",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_runs_asset", "asset_runs", ["asset_id", "queued_at"])


def _create_tasks() -> None:
    op.create_table(
        "asset_run_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log", sa.Text(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.CheckConstraint(_TASK_STATE, name="ck_asset_run_tasks_state"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["asset_runs.id"],
            name="fk_asset_run_tasks_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "task_id", name="uq_asset_run_tasks_run_task"),
    )
    op.create_index("ix_asset_run_tasks_run", "asset_run_tasks", ["run_id"])


def _create_outputs() -> None:
    op.create_table(
        "asset_outputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("markdown_key", sa.String(length=1024), nullable=False),
        sa.Column("json_key", sa.String(length=1024), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_asset_outputs_size"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_asset_outputs_asset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["asset_runs.id"],
            name="fk_asset_outputs_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_outputs_asset", "asset_outputs", ["asset_id", "created_at"])
    op.create_index("ix_asset_outputs_run", "asset_outputs", ["run_id"])


def _release_indexes(bind: Connection) -> None:
    """Give leftover asset index names to the agent tables that still own them."""
    old: str
    new: str
    for old, new in _RELEASED_INDEXES:
        _rename_index(bind, old, new)


def _rename_index(bind: Connection, old: str, new: str) -> None:
    """Rename one index. Missing or already-renamed indexes are left alone."""
    if _index_exists(bind, new) or not _index_exists(bind, old):
        return
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f'ALTER INDEX "{old}" RENAME TO "{new}"'))
        return
    stored: object = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": old},
    ).scalar()
    if not isinstance(stored, str) or stored == "":
        return
    op.execute(sa.text(f"DROP INDEX {old}"))
    op.execute(sa.text(stored.replace(f"INDEX {old} ", f"INDEX {new} ", 1)))


def _index_exists(bind: Connection, name: str) -> bool:
    if bind.dialect.name == "postgresql":
        found: object = bind.execute(
            sa.text("SELECT 1 FROM pg_class WHERE relkind = 'i' AND relname = :name"),
            {"name": name},
        ).scalar()
        return found is not None
    stored: object = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": name},
    ).scalar()
    return stored is not None


def _free_agent_constraint(bind: Connection, old: str, new: str, expression: str) -> None:
    """Move a leftover check name off ``agents`` so the new assets table can use it."""
    if "agents" not in _tables(bind):
        return
    if _check_sql(bind, "agents", old) is None or _check_sql(bind, "agents", new) is not None:
        return
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint(old, type_="check")
        batch.create_check_constraint(new, expression)


def _restore_agent_columns(bind: Connection) -> None:
    if "agent_definition_items" in _tables(bind):
        present: set[str] = _column_names(bind, "agent_definition_items")
        with op.batch_alter_table("agent_definition_items") as batch:
            if "run_status" not in present:
                batch.add_column(sa.Column("run_status", sa.String(length=16), nullable=True))
            if "run_error" not in present:
                batch.add_column(sa.Column("run_error", sa.Text(), nullable=True))
            if "ran_at" not in present:
                batch.add_column(sa.Column("ran_at", sa.DateTime(timezone=True), nullable=True))
            if "schedule" not in present:
                batch.add_column(sa.Column("schedule", sa.String(length=128), nullable=True))
            if "next_run_at" not in present:
                batch.add_column(
                    sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
                )
            if "ck_agent_definition_items_run_status" not in _check_names(
                bind,
                "agent_definition_items",
            ):
                batch.create_check_constraint("ck_agent_definition_items_run_status", _ITEM_RUN)
    if "agent_operator_runs" not in _tables(bind):
        return
    if "item_id" in _column_names(bind, "agent_operator_runs"):
        return
    with op.batch_alter_table("agent_operator_runs") as batch:
        batch.add_column(sa.Column("item_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_agent_operator_runs_item",
            "agent_definition_items",
            ["item_id"],
            ["id"],
            ondelete="SET NULL",
        )


def _drop_columns(bind: Connection, table: str, names: tuple[str, ...]) -> None:
    if table not in _tables(bind):
        return
    present: set[str] = _column_names(bind, table)
    targets: set[str] = {name for name in names if name in present}
    if len(targets) == 0:
        return
    inspector: Inspector = inspect(bind)
    with op.batch_alter_table(table) as batch:
        changer: _Batch = cast(_Batch, batch)
        reflected: object
        for reflected in inspector.get_check_constraints(table):
            item: Mapping[str, object] = cast(Mapping[str, object], reflected)
            sqltext: object = item.get("sqltext")
            label: object = item.get("name")
            if not isinstance(label, str) or not isinstance(sqltext, str):
                continue
            if any(target in sqltext for target in targets):
                changer.drop_constraint(label, type_="check")
        for reflected in inspector.get_foreign_keys(table):
            _drop_named(changer, reflected, "constrained_columns", targets, "foreignkey")
        for reflected in inspector.get_indexes(table):
            mapped: Mapping[str, object] = cast(Mapping[str, object], reflected)
            if mapped.get("unique") is True:
                continue
            _drop_named(changer, reflected, "column_names", targets, "index")
        name: str
        for name in names:
            if name in present:
                changer.drop_column(name)


def _drop_named(
    batch: _Batch,
    reflected: object,
    column_key: str,
    targets: set[str],
    kind: str,
) -> None:
    item: Mapping[str, object] = cast(Mapping[str, object], reflected)
    label: object = item.get("name")
    columns: object = item.get(column_key)
    if not isinstance(label, str) or not isinstance(columns, list):
        return
    names: set[str] = {str(column) for column in columns}
    if targets.isdisjoint(names):
        return
    if kind == "index":
        batch.drop_index(label)
        return
    batch.drop_constraint(label, type_=kind)


def _tables(bind: Connection) -> set[str]:
    inspector: Inspector = inspect(bind)
    return set(inspector.get_table_names())


def _column_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    return {str(column["name"]) for column in inspector.get_columns(table)}


def _check_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    found: set[str] = set()
    reflected: object
    for reflected in inspector.get_check_constraints(table):
        item: Mapping[str, object] = cast(Mapping[str, object], reflected)
        label: object = item.get("name")
        if isinstance(label, str):
            found.add(label)
    return found


def _check_sql(bind: Connection, table: str, name: str) -> str | None:
    inspector: Inspector = inspect(bind)
    reflected: object
    for reflected in inspector.get_check_constraints(table):
        item: Mapping[str, object] = cast(Mapping[str, object], reflected)
        if item.get("name") != name:
            continue
        sqltext: object = item.get("sqltext")
        if isinstance(sqltext, str):
            return sqltext
    return None
