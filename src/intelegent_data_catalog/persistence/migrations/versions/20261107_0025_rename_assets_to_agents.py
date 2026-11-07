"""Rename catalog assets to agents.

Revision 0001 creates the current models on a fresh database, so those
databases already use the agent names and this revision does nothing.
A database created before the rename still has the asset tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261107_0025"
down_revision: str | None = "20261028_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_CONSTRAINT: str = "ck_catalog_jobs_kind"
_JOB_AGENT: str = "kind IN ('knowledge', 'context', 'airflow', 'agent')"
_JOB_ASSET: str = "kind IN ('knowledge', 'context', 'airflow', 'asset')"
_FILE_CONSTRAINT: str = "ck_agent_definition_files_kind"
_FILE_CONSTRAINT_OLD: str = "ck_asset_definition_files_kind"
_ITEM_CONSTRAINT: str = "ck_agent_definition_items_kind"
_ITEM_CONSTRAINT_OLD: str = "ck_asset_definition_items_kind"
_KIND_AGENT: str = "kind IN ('AGENT')"
_KIND_ASSET: str = "kind IN ('ASSET')"
_FOLDER_AGENT: str = "folder_kind IN ('AGENT')"
_FOLDER_ASSET: str = "folder_kind IN ('ASSET')"

_TABLES: tuple[tuple[str, str], ...] = (
    ("assets", "agents"),
    ("asset_knowledge_contexts", "agent_knowledge_contexts"),
    ("asset_engine_bindings", "agent_engine_bindings"),
    ("asset_definition_files", "agent_definition_files"),
    ("asset_definition_items", "agent_definition_items"),
    ("asset_operator_runs", "agent_operator_runs"),
    ("asset_operator_run_tasks", "agent_operator_run_tasks"),
)
_INDEXES: tuple[tuple[str, str], ...] = (
    ("uq_assets_owner_slug", "uq_agents_owner_slug"),
    ("uq_assets_shared_slug", "uq_agents_shared_slug"),
    ("ix_assets_kind", "ix_agents_kind"),
    ("ix_assets_workspace_owner", "ix_agents_workspace_owner"),
    ("ix_assets_updated_at", "ix_agents_updated_at"),
    ("uq_asset_knowledge_contexts_asset", "uq_agent_knowledge_contexts_agent"),
    ("uq_catalog_tables_asset", "uq_catalog_tables_agent"),
    ("uq_airflow_instances_asset", "uq_airflow_instances_agent"),
    ("uq_airflow_dags_asset", "uq_airflow_dags_agent"),
    ("uq_workspace_nodes_asset", "uq_workspace_nodes_agent"),
    ("uq_entity_configurations_asset", "uq_entity_configurations_agent"),
    ("ix_asset_definition_items_asset", "ix_agent_definition_items_agent"),
    ("ix_asset_operator_runs_asset", "ix_agent_operator_runs_agent"),
)
_ID_TABLES: tuple[str, ...] = (
    "asset_knowledge_contexts",
    "catalog_tables",
    "airflow_dags",
    "airflow_instances",
    "workspace_nodes",
    "entity_configurations",
    "asset_definition_items",
    "asset_operator_runs",
)


def upgrade() -> None:
    """Rename asset tables and columns, and store agent enum values."""
    bind: Connection = op.get_bind()
    tables: set[str] = _tables(bind)
    if "assets" not in tables:
        return
    _jobs(bind, "asset", "agent", _JOB_AGENT)
    _definition_kind(
        bind,
        "asset_definition_files",
        "folder_kind",
        _FILE_CONSTRAINT_OLD,
        _FILE_CONSTRAINT,
        _FOLDER_AGENT,
    )
    _definition_kind(
        bind,
        "asset_definition_items",
        "kind",
        _ITEM_CONSTRAINT_OLD,
        _ITEM_CONSTRAINT,
        _KIND_AGENT,
    )
    table: str
    for table in _ID_TABLES:
        _rename_column(bind, table, "asset_id", "agent_id")
    old: str
    new: str
    for old, new in _TABLES:
        if old in _tables(bind) and new not in _tables(bind):
            op.rename_table(old, new)
    prior: str
    current: str
    for prior, current in _INDEXES:
        _rename_index(bind, prior, current)


def downgrade() -> None:
    """Restore the asset table names and enum values."""
    bind: Connection = op.get_bind()
    tables: set[str] = _tables(bind)
    if "agents" not in tables:
        return
    current: str
    prior: str
    for prior, current in _INDEXES:
        _rename_index(bind, current, prior)
    new: str
    old: str
    for old, new in reversed(_TABLES):
        if new in _tables(bind) and old not in _tables(bind):
            op.rename_table(new, old)
    renamed: str
    for renamed in (
        "asset_knowledge_contexts",
        "catalog_tables",
        "airflow_dags",
        "airflow_instances",
        "workspace_nodes",
        "entity_configurations",
        "asset_definition_items",
        "asset_operator_runs",
    ):
        _rename_column(bind, renamed, "agent_id", "asset_id")
    _definition_kind(
        bind,
        "asset_definition_files",
        "folder_kind",
        _FILE_CONSTRAINT,
        _FILE_CONSTRAINT_OLD,
        _FOLDER_ASSET,
    )
    _definition_kind(
        bind,
        "asset_definition_items",
        "kind",
        _ITEM_CONSTRAINT,
        _ITEM_CONSTRAINT_OLD,
        _KIND_ASSET,
    )
    _jobs(bind, "agent", "asset", _JOB_ASSET)


def _jobs(bind: Connection, source: str, target: str, expression: str) -> None:
    if "catalog_jobs" not in _tables(bind):
        return
    sqltext: str | None = _check_sql(bind, "catalog_jobs", _JOB_CONSTRAINT)
    if sqltext is None or f"'{source}'" not in sqltext:
        return
    with op.batch_alter_table("catalog_jobs") as batch:
        batch.drop_constraint(_JOB_CONSTRAINT, type_="check")
    bind.execute(
        sa.text(f"UPDATE catalog_jobs SET kind = '{target}' WHERE kind = '{source}'"),
    )
    with op.batch_alter_table("catalog_jobs") as batch:
        batch.create_check_constraint(_JOB_CONSTRAINT, expression)


def _definition_kind(
    bind: Connection,
    table: str,
    column: str,
    old_name: str,
    new_name: str,
    expression: str,
) -> None:
    if table not in _tables(bind):
        return
    sqltext: str | None = _check_sql(bind, table, old_name)
    if sqltext is None:
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(old_name, type_="check")
    if "ASSET" in sqltext:
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = 'AGENT' WHERE {column} = 'ASSET'"),
        )
    elif "AGENT" in sqltext:
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = 'ASSET' WHERE {column} = 'AGENT'"),
        )
    with op.batch_alter_table(table) as batch:
        batch.create_check_constraint(new_name, expression)


def _rename_index(bind: Connection, old: str, new: str) -> None:
    """Rename one index. SQLite rewrites the stored statement; Postgres uses ALTER INDEX."""
    if bind.dialect.name == "postgresql":
        found: object = bind.execute(
            sa.text("SELECT 1 FROM pg_class WHERE relkind = 'i' AND relname = :name"),
            {"name": old},
        ).scalar()
        if found is None:
            return
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


def _rename_column(bind: Connection, table: str, old: str, new: str) -> None:
    if table not in _tables(bind):
        return
    present: set[str] = _column_names(bind, table)
    if old not in present or new in present:
        return
    # RENAME COLUMN rewrites indexes and checks. Batch recreate would keep the
    # model's partial-index predicate on the new name while the column list
    # still used the old name.
    op.execute(sa.text(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"))


def _check_sql(bind: Connection, table: str, name: str) -> str | None:
    if table not in _tables(bind):
        return None
    inspector: Inspector = inspect(bind)
    check: dict[str, object]
    for check in inspector.get_check_constraints(table):
        if check.get("name") != name:
            continue
        sqltext: object = check.get("sqltext")
        if isinstance(sqltext, str):
            return sqltext
        return None
    return None


def _tables(bind: Connection) -> set[str]:
    inspector: Inspector = inspect(bind)
    return set(inspector.get_table_names())


def _column_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    return {str(column["name"]) for column in inspector.get_columns(table)}
