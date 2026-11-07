"""History of asset operator runs and their tasks.

Revision ID: 20261023_0022
Revises: 20261022_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261023_0022"
down_revision: str | None = "20261022_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUN_STATUS: str = "status IN ('queued', 'running', 'succeeded', 'failed')"
_TRIGGER: str = "trigger IN ('apply', 'schedule', 'manual')"
_TASK_STATE: str = (
    "state IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'upstream_failed')"
)


def upgrade() -> None:
    """Add run history tables when a database was created before this revision."""
    bind: Connection = op.get_bind()
    tables: set[str] = _tables(bind)
    if "agent_operator_runs" in tables or "agent_operator_run_tasks" in tables:
        return
    if "asset_operator_runs" not in tables:
        _create_runs()
    if "asset_operator_run_tasks" not in tables:
        _create_tasks()


def downgrade() -> None:
    """Drop asset operator run history."""
    bind: Connection = op.get_bind()
    tables: set[str] = _tables(bind)
    if "asset_operator_run_tasks" in tables:
        op.drop_table("asset_operator_run_tasks")
    if "asset_operator_runs" in tables:
        op.drop_table("asset_operator_runs")


def _create_runs() -> None:
    op.create_table(
        "asset_operator_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("catalog_job_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("try_number", sa.Integer(), nullable=False),
        sa.Column("operator_type", sa.String(length=255), nullable=True),
        sa.Column("operator_params", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_RUN_STATUS, name="ck_asset_operator_runs_status"),
        sa.CheckConstraint(_TRIGGER, name="ck_asset_operator_runs_trigger"),
        sa.CheckConstraint("try_number >= 1", name="ck_asset_operator_runs_try"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_asset_operator_runs_asset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["asset_definition_items.id"],
            name="fk_asset_operator_runs_item",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_job_id"],
            ["catalog_jobs.id"],
            name="fk_asset_operator_runs_job",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_asset_operator_runs_asset",
        "asset_operator_runs",
        ["asset_id", "queued_at"],
    )


def _create_tasks() -> None:
    op.create_table(
        "asset_operator_run_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log", sa.Text(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.CheckConstraint(_TASK_STATE, name="ck_asset_operator_run_tasks_state"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["asset_operator_runs.id"],
            name="fk_asset_operator_run_tasks_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "task_id", name="uq_asset_operator_run_tasks_run_task"),
    )
    op.create_index(
        "ix_asset_operator_run_tasks_run",
        "asset_operator_run_tasks",
        ["run_id"],
    )


def _tables(bind: Connection) -> set[str]:
    inspector: Inspector = inspect(bind)
    return set(inspector.get_table_names())
