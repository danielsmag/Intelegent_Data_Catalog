"""Space knowledge snapshots and business context on spaces.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the columns and the snapshot table when that database
was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261011_0006"
down_revision: str | None = "20261010_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORIGIN_CHECK: str = "context_origin IN ('USER', 'LLM')"
_ORIGIN_CONSTRAINT: str = "ck_spaces_context_origin"


def upgrade() -> None:
    """Add space context columns and the space snapshot table."""
    bind: Connection = op.get_bind()
    _add_context(bind)
    _create_space_contexts(bind)


def downgrade() -> None:
    """Drop the space snapshot table and the space context columns."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "space_knowledge_contexts" in tables:
        op.drop_table("space_knowledge_contexts")
    _drop_context(bind)


def _add_context(bind: Connection) -> None:
    inspector = inspect(bind)
    if "spaces" not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns("spaces")}
    if "context" in present and "context_origin" in present:
        return
    with op.batch_alter_table("spaces") as batch:
        if "context" not in present:
            batch.add_column(sa.Column("context", sa.Text(), nullable=True))
        if "context_origin" not in present:
            batch.add_column(sa.Column("context_origin", sa.String(length=32), nullable=True))
        batch.create_check_constraint(_ORIGIN_CONSTRAINT, _ORIGIN_CHECK)


def _drop_context(bind: Connection) -> None:
    inspector = inspect(bind)
    if "spaces" not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns("spaces")}
    checks: set[str] = set()
    check_name: str | None
    for check_name in (item.get("name") for item in inspector.get_check_constraints("spaces")):
        if check_name is not None:
            checks.add(check_name)
    if (
        "context" not in present
        and "context_origin" not in present
        and _ORIGIN_CONSTRAINT not in checks
    ):
        return
    with op.batch_alter_table("spaces") as batch:
        if _ORIGIN_CONSTRAINT in checks:
            batch.drop_constraint(_ORIGIN_CONSTRAINT, type_="check")
        if "context_origin" in present:
            batch.drop_column("context_origin")
        if "context" in present:
            batch.drop_column("context")


def _create_space_contexts(bind: Connection) -> None:
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "space_knowledge_contexts" in tables:
        return
    op.create_table(
        "space_knowledge_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", name="uq_space_knowledge_contexts_space"),
    )
