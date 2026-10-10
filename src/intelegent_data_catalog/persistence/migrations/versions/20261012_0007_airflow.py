"""Airflow instances, DAG snapshots, table mappings, and sync runs.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the Airflow tables when that database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261012_0007"
down_revision: str | None = "20261011_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CHECK: str = "status IN ('ACTIVE', 'DISABLED')"
_SYNC_STATUS_CHECK: str = "status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')"
_ORIGIN_CHECK: str = "origin IN ('ASSET', 'SOURCE_MATCH', 'MANUAL')"
_DIRECTION_CHECK: str = "direction IN ('INPUT', 'OUTPUT', 'UNKNOWN')"


def upgrade() -> None:
    """Add the Airflow registry tables."""
    bind: Connection = op.get_bind()
    _create_instances(bind)
    _create_dags(bind)
    _create_dag_tables(bind)
    _create_sync_runs(bind)


def downgrade() -> None:
    """Drop the Airflow registry tables."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "airflow_dag_tables" in tables:
        op.drop_table("airflow_dag_tables")
    if "airflow_sync_runs" in tables:
        op.drop_table("airflow_sync_runs")
    if "airflow_dags" in tables:
        op.drop_table("airflow_dags")
    if "airflow_instances" in tables:
        op.drop_table("airflow_instances")


def _create_instances(bind: Connection) -> None:
    inspector = inspect(bind)
    if "airflow_instances" in set(inspector.get_table_names()):
        return
    op.create_table(
        "airflow_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password", sa.Text(), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_airflow_instances_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("base_url", name="uq_airflow_instances_base_url"),
        sa.UniqueConstraint("slug"),
    )


def _create_dags(bind: Connection) -> None:
    inspector = inspect(bind)
    if "airflow_dags" in set(inspector.get_table_names()):
        return
    op.create_table(
        "airflow_dags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("dag_id", sa.String(length=250), nullable=False),
        sa.Column("display_name", sa.String(length=1024), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_paused", sa.Boolean(), nullable=False),
        sa.Column("is_stale", sa.Boolean(), nullable=False),
        sa.Column("has_import_errors", sa.Boolean(), nullable=False),
        sa.Column("timetable_summary", sa.String(length=1024), nullable=True),
        sa.Column("timetable_description", sa.Text(), nullable=True),
        sa.Column("owners", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("fileloc", sa.String(length=2048), nullable=True),
        sa.Column("bundle_name", sa.String(length=255), nullable=True),
        sa.Column("max_active_runs", sa.Integer(), nullable=True),
        sa.Column("next_dagrun_run_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_parsed_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("last_run", sa.JSON(), nullable=True),
        sa.Column("run_stats", sa.JSON(), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=True),
        sa.Column("source_version", sa.Integer(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["airflow_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id", "dag_id", name="uq_airflow_dags_instance_dag"),
    )


def _create_dag_tables(bind: Connection) -> None:
    inspector = inspect(bind)
    if "airflow_dag_tables" in set(inspector.get_table_names()):
        return
    op.create_table(
        "airflow_dag_tables",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dag_row_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_table_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.CheckConstraint(_ORIGIN_CHECK, name="ck_airflow_dag_tables_origin"),
        sa.CheckConstraint(_DIRECTION_CHECK, name="ck_airflow_dag_tables_direction"),
        sa.ForeignKeyConstraint(["catalog_table_id"], ["catalog_tables.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dag_row_id"], ["airflow_dags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dag_row_id",
            "catalog_table_id",
            "origin",
            name="uq_airflow_dag_tables_link",
        ),
    )
    op.create_index("ix_airflow_dag_tables_table", "airflow_dag_tables", ["catalog_table_id"])


def _create_sync_runs(bind: Connection) -> None:
    inspector = inspect(bind)
    if "airflow_sync_runs" in set(inspector.get_table_names()):
        return
    op.create_table(
        "airflow_sync_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("sanitized_error", sa.Text(), nullable=True),
        sa.CheckConstraint(_SYNC_STATUS_CHECK, name="ck_airflow_sync_runs_status"),
        sa.ForeignKeyConstraint(["instance_id"], ["airflow_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
