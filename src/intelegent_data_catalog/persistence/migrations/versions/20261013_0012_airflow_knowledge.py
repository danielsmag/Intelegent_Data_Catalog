"""Airflow attachments, instance knowledge, and LLM DAG links.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the tables and widens checks when that database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261013_0012"
down_revision: str | None = "20261013_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORIGIN_CHECK: str = "context_origin IN ('USER', 'LLM')"
_CONTEXT_CONSTRAINT: str = "ck_airflow_instances_context_origin"
_MAPPING_CONSTRAINT: str = "ck_airflow_dag_tables_origin"
_MAPPING_WITH_LLM: str = "origin IN ('ASSET', 'SOURCE_MATCH', 'MANUAL', 'LLM')"
_MAPPING_WITHOUT_LLM: str = "origin IN ('ASSET', 'SOURCE_MATCH', 'MANUAL')"
_JOB_CONSTRAINT: str = "ck_catalog_jobs_kind"
_JOB_WITH_AIRFLOW: str = "kind IN ('knowledge', 'scope', 'space', 'context', 'airflow')"
_JOB_WITHOUT_AIRFLOW: str = "kind IN ('knowledge', 'scope', 'space', 'context')"
_ONE_SOURCE: str = (
    "(airflow_instance_id IS NOT NULL AND dag_row_id IS NULL) OR "
    "(airflow_instance_id IS NULL AND dag_row_id IS NOT NULL)"
)
_ONE_TARGET: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL) OR (space_id IS NULL AND scope_id IS NOT NULL)"
)
_PARTIAL_INDEXES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "uq_airflow_attachments_instance_space",
        ("airflow_instance_id", "space_id"),
        "airflow_instance_id IS NOT NULL AND space_id IS NOT NULL",
    ),
    (
        "uq_airflow_attachments_instance_scope",
        ("airflow_instance_id", "scope_id"),
        "airflow_instance_id IS NOT NULL AND scope_id IS NOT NULL",
    ),
    (
        "uq_airflow_attachments_dag_space",
        ("dag_row_id", "space_id"),
        "dag_row_id IS NOT NULL AND space_id IS NOT NULL",
    ),
    (
        "uq_airflow_attachments_dag_scope",
        ("dag_row_id", "scope_id"),
        "dag_row_id IS NOT NULL AND scope_id IS NOT NULL",
    ),
)


def upgrade() -> None:
    """Add Airflow attachment storage and allow LLM DAG links."""
    bind: Connection = op.get_bind()
    _add_context(bind)
    _create_attachments(bind)
    _create_instance_contexts(bind)
    if not _check_contains(bind, "airflow_dag_tables", _MAPPING_CONSTRAINT, "LLM"):
        _replace_check("airflow_dag_tables", _MAPPING_CONSTRAINT, _MAPPING_WITH_LLM)
    if not _check_contains(bind, "catalog_jobs", _JOB_CONSTRAINT, "airflow"):
        _replace_check("catalog_jobs", _JOB_CONSTRAINT, _JOB_WITH_AIRFLOW)


def downgrade() -> None:
    """Drop Airflow attachment storage and remove the widened check values."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "airflow_instance_knowledge_contexts" in tables:
        op.drop_table("airflow_instance_knowledge_contexts")
    if "airflow_attachments" in tables:
        op.drop_table("airflow_attachments")
    _drop_context(bind)
    if _check_contains(bind, "airflow_dag_tables", _MAPPING_CONSTRAINT, "LLM"):
        _replace_check("airflow_dag_tables", _MAPPING_CONSTRAINT, _MAPPING_WITHOUT_LLM)
    if _check_contains(bind, "catalog_jobs", _JOB_CONSTRAINT, "airflow"):
        _replace_check("catalog_jobs", _JOB_CONSTRAINT, _JOB_WITHOUT_AIRFLOW)


def _add_context(bind: Connection) -> None:
    inspector = inspect(bind)
    if "airflow_instances" not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns("airflow_instances")}
    if "context" in present and "context_origin" in present:
        return
    with op.batch_alter_table("airflow_instances") as batch:
        if "context" not in present:
            batch.add_column(sa.Column("context", sa.Text(), nullable=True))
        if "context_origin" not in present:
            batch.add_column(sa.Column("context_origin", sa.String(length=32), nullable=True))
        batch.create_check_constraint(_CONTEXT_CONSTRAINT, _ORIGIN_CHECK)


def _drop_context(bind: Connection) -> None:
    inspector = inspect(bind)
    if "airflow_instances" not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns("airflow_instances")}
    checks: set[str] = _check_names(bind, "airflow_instances")
    if (
        "context" not in present
        and "context_origin" not in present
        and _CONTEXT_CONSTRAINT not in checks
    ):
        return
    with op.batch_alter_table("airflow_instances") as batch:
        if _CONTEXT_CONSTRAINT in checks:
            batch.drop_constraint(_CONTEXT_CONSTRAINT, type_="check")
        if "context_origin" in present:
            batch.drop_column("context_origin")
        if "context" in present:
            batch.drop_column("context")


def _create_attachments(bind: Connection) -> None:
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "airflow_attachments" in tables:
        return
    if "spaces" not in tables or "scopes" not in tables:
        return
    op.create_table(
        "airflow_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("airflow_instance_id", sa.Uuid(), nullable=True),
        sa.Column("dag_row_id", sa.Uuid(), nullable=True),
        sa.Column("space_id", sa.Uuid(), nullable=True),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ONE_SOURCE, name="ck_airflow_attachments_one_source"),
        sa.CheckConstraint(_ONE_TARGET, name="ck_airflow_attachments_one_target"),
        sa.ForeignKeyConstraint(
            ["airflow_instance_id"],
            ["airflow_instances.id"],
            name="fk_airflow_attachments_instance",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dag_row_id"],
            ["airflow_dags.id"],
            name="fk_airflow_attachments_dag",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name="fk_airflow_attachments_space",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scopes.id"],
            name="fk_airflow_attachments_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    name: str
    columns: tuple[str, ...]
    predicate: str
    for name, columns, predicate in _PARTIAL_INDEXES:
        op.create_index(
            name,
            "airflow_attachments",
            list(columns),
            unique=True,
            sqlite_where=sa.text(predicate),
            postgresql_where=sa.text(predicate),
        )


def _create_instance_contexts(bind: Connection) -> None:
    inspector = inspect(bind)
    if "airflow_instance_knowledge_contexts" in set(inspector.get_table_names()):
        return
    op.create_table(
        "airflow_instance_knowledge_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["airflow_instances.id"],
            name="fk_airflow_instance_knowledge_contexts_instance",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id", name="uq_airflow_instance_knowledge_contexts_instance"),
    )


def _check_contains(bind: Connection, table: str, name: str, token: str) -> bool:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return False
    check: dict[str, object]
    for check in inspector.get_check_constraints(table):
        if check.get("name") != name:
            continue
        sqltext: object = check.get("sqltext")
        return isinstance(sqltext, str) and token in sqltext
    return False


def _check_names(bind: Connection, table: str) -> set[str]:
    inspector = inspect(bind)
    found: set[str] = set()
    check_name: str | None
    for check_name in (item.get("name") for item in inspector.get_check_constraints(table)):
        if check_name is not None:
            found.add(check_name)
    return found


def _replace_check(table: str, name: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)
