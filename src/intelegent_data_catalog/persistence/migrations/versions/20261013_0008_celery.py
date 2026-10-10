"""Sync schedules and background catalog jobs.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the scheduler tables when that database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261013_0008"
down_revision: str | None = "20261012_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONE_TARGET: str = (
    "(source_engine_binding_id IS NOT NULL AND airflow_instance_id IS NULL) OR "
    "(source_engine_binding_id IS NULL AND airflow_instance_id IS NOT NULL)"
)
_JOB_KIND_CHECK: str = "kind IN ('knowledge', 'scope', 'space', 'context')"
_JOB_STATUS_CHECK: str = "status IN ('running', 'succeeded', 'failed')"
_RUNNING_INDEX: str = (
    "CREATE UNIQUE INDEX uq_catalog_jobs_running ON catalog_jobs (kind, subject_id, lane) "
    "WHERE status = 'running'"
)


def upgrade() -> None:
    """Add schedule and background-job tables."""
    bind: Connection = op.get_bind()
    _create_schedules(bind)
    _create_jobs(bind)


def downgrade() -> None:
    """Drop schedule and background-job tables."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "catalog_jobs" in tables:
        op.drop_table("catalog_jobs")
    if "sync_schedules" in tables:
        op.drop_table("sync_schedules")


def _create_schedules(bind: Connection) -> None:
    inspector = inspect(bind)
    if "sync_schedules" in set(inspector.get_table_names()):
        return
    op.create_table(
        "sync_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_engine_binding_id", sa.Uuid(), nullable=True),
        sa.Column("airflow_instance_id", sa.Uuid(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ONE_TARGET, name="ck_sync_schedules_one_target"),
        sa.CheckConstraint("interval_seconds >= 1", name="ck_sync_schedules_interval"),
        sa.ForeignKeyConstraint(
            ["source_engine_binding_id"],
            ["source_engine_bindings.id"],
            name="fk_sync_schedules_binding",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["airflow_instance_id"],
            ["airflow_instances.id"],
            name="fk_sync_schedules_instance",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_engine_binding_id", name="uq_sync_schedules_binding"),
        sa.UniqueConstraint("airflow_instance_id", name="uq_sync_schedules_instance"),
    )


def _create_jobs(bind: Connection) -> None:
    inspector = inspect(bind)
    if "catalog_jobs" in set(inspector.get_table_names()):
        return
    op.create_table(
        "catalog_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("subject_name", sa.String(length=1024), nullable=False),
        sa.Column("lane", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_JOB_KIND_CHECK, name="ck_catalog_jobs_kind"),
        sa.CheckConstraint(_JOB_STATUS_CHECK, name="ck_catalog_jobs_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text(_RUNNING_INDEX))
