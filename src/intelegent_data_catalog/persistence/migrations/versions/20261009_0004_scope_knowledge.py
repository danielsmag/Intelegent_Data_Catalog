"""Scope knowledge snapshots and LLM relationship origin.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the snapshot table and the LLM origin when that database
was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261009_0004"
down_revision: str | None = "20261007_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORIGIN_WITH_LLM: str = (
    "origin IN ('DATABASE_CONSTRAINT', 'MANUAL', 'IMPORT', 'LLM')"
)
_ORIGIN_WITHOUT_LLM: str = "origin IN ('DATABASE_CONSTRAINT', 'MANUAL', 'IMPORT')"
_ORIGIN_CONSTRAINT: str = "ck_relationships_origin"


def upgrade() -> None:
    """Add the scope snapshot table and allow the LLM relationship origin."""
    bind: Connection = op.get_bind()
    _create_scope_contexts(bind)
    if not _origin_sql_contains(bind, "LLM"):
        _replace_origin_check(_ORIGIN_WITH_LLM)


def downgrade() -> None:
    """Drop the scope snapshot table and remove the LLM origin check value."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "scope_knowledge_contexts" in tables:
        op.drop_table("scope_knowledge_contexts")
    if _origin_sql_contains(bind, "LLM"):
        _replace_origin_check(_ORIGIN_WITHOUT_LLM)


def _create_scope_contexts(bind: Connection) -> None:
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "scope_knowledge_contexts" in tables or "scopes" not in tables:
        return
    op.create_table(
        "scope_knowledge_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_id", name="uq_scope_knowledge_contexts_scope"),
    )


def _origin_sql_contains(bind: Connection, token: str) -> bool:
    inspector = inspect(bind)
    for check in inspector.get_check_constraints("relationships"):
        if check.get("name") != _ORIGIN_CONSTRAINT:
            continue
        sqltext: object = check.get("sqltext")
        return isinstance(sqltext, str) and token in sqltext
    return False


def _replace_origin_check(expression: str) -> None:
    with op.batch_alter_table("relationships") as batch:
        batch.drop_constraint(_ORIGIN_CONSTRAINT, type_="check")
        batch.create_check_constraint(_ORIGIN_CONSTRAINT, expression)
