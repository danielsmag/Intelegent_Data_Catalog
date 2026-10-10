"""Saved refresh settings for spaces, scopes, and tables.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the table when that database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261013_0011"
down_revision: str | None = "20261013_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONE_SUBJECT: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL AND catalog_table_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NOT NULL AND catalog_table_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NULL AND catalog_table_id IS NOT NULL)"
)


def upgrade() -> None:
    """Add entity configuration storage."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    if "entity_configurations" in set(inspector.get_table_names()):
        return
    op.create_table(
        "entity_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=True),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("catalog_table_id", sa.Uuid(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ONE_SUBJECT, name="ck_entity_configurations_one_subject"),
        sa.CheckConstraint("interval_seconds >= 1", name="ck_entity_configurations_interval"),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name="fk_entity_configurations_space",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scopes.id"],
            name="fk_entity_configurations_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_table_id"],
            ["catalog_tables.id"],
            name="fk_entity_configurations_table",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", name="uq_entity_configurations_space"),
        sa.UniqueConstraint("scope_id", name="uq_entity_configurations_scope"),
        sa.UniqueConstraint("catalog_table_id", name="uq_entity_configurations_table"),
    )


def downgrade() -> None:
    """Drop entity configuration storage."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    if "entity_configurations" not in set(inspector.get_table_names()):
        return
    op.drop_table("entity_configurations")
