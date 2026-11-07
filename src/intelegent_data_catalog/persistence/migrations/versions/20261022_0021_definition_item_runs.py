"""Operator run columns on asset definition items.

Revision ID: 20261022_0021
Revises: 20261021_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261022_0021"
down_revision: str | None = "20261021_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE: str = "asset_definition_items"
_RUN_CHECK: str = "run_status IS NULL OR run_status IN ('queued', 'running', 'succeeded', 'failed')"
_RUN_CONSTRAINT: str = "ck_asset_definition_items_run_status"
_RUN_CONSTRAINTS: tuple[str, ...] = (
    "ck_asset_definition_items_run_status",
    "ck_agent_definition_items_run_status",
)
_NEXT_INDEX: str = "ix_asset_definition_items_next_run"
_NEXT_INDEXES: tuple[str, ...] = (
    "ix_asset_definition_items_next_run",
    "ix_agent_definition_items_next_run",
)


def upgrade() -> None:
    """Add nullable run and schedule columns. Existing rows stay null."""
    bind: Connection = op.get_bind()
    if _TABLE not in _tables(bind):
        return
    present: set[str] = _column_names(bind, _TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        if "run_status" not in present:
            batch.add_column(sa.Column("run_status", sa.String(length=16), nullable=True))
        if "run_error" not in present:
            batch.add_column(sa.Column("run_error", sa.Text(), nullable=True))
        if "ran_at" not in present:
            batch.add_column(sa.Column("ran_at", sa.DateTime(timezone=True), nullable=True))
        if "schedule" not in present:
            batch.add_column(sa.Column("schedule", sa.String(length=128), nullable=True))
        if "next_run_at" not in present:
            batch.add_column(sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
        if _RUN_CONSTRAINT not in _check_names(bind, _TABLE):
            batch.create_check_constraint(_RUN_CONSTRAINT, _RUN_CHECK)
    if _NEXT_INDEX not in _index_names(bind, _TABLE):
        op.create_index(
            _NEXT_INDEX,
            _TABLE,
            ["next_run_at"],
            sqlite_where=sa.text("next_run_at IS NOT NULL"),
            postgresql_where=sa.text("next_run_at IS NOT NULL"),
        )


def downgrade() -> None:
    """Drop the operator run columns."""
    bind: Connection = op.get_bind()
    if _TABLE not in _tables(bind):
        return
    indexes: set[str] = _index_names(bind, _TABLE)
    index_name: str
    for index_name in _NEXT_INDEXES:
        if index_name in indexes:
            op.drop_index(index_name, table_name=_TABLE)
    present: set[str] = _column_names(bind, _TABLE)
    checks: set[str] = _check_names(bind, _TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        constraint: str
        for constraint in _RUN_CONSTRAINTS:
            if constraint in checks:
                batch.drop_constraint(constraint, type_="check")
        name: str
        for name in ("next_run_at", "schedule", "ran_at", "run_error", "run_status"):
            if name in present:
                batch.drop_column(name)


def _tables(bind: Connection) -> set[str]:
    inspector: Inspector = inspect(bind)
    return set(inspector.get_table_names())


def _column_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    return {str(column["name"]) for column in inspector.get_columns(table)}


def _index_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    found: set[str] = set()
    for item in inspector.get_indexes(table):
        label: object = item.get("name")
        if isinstance(label, str):
            found.add(label)
    return found


def _check_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    found: set[str] = set()
    for item in inspector.get_check_constraints(table):
        label: object = item.get("name")
        if isinstance(label, str):
            found.add(label)
    return found
