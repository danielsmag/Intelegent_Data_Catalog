"""Saved table analysis context.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the snapshot table when that database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261007_0003"
down_revision: str | None = "20261002_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the knowledge-context snapshot table when it is missing."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "table_knowledge_contexts" in tables:
        return
    op.create_table(
        "table_knowledge_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["table_id"], ["catalog_tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_id", name="uq_table_knowledge_contexts_table"),
    )


def downgrade() -> None:
    """Drop the knowledge-context snapshot table."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "table_knowledge_contexts" in tables:
        op.drop_table("table_knowledge_contexts")
