"""Tags, Marquez mappings, and column observation time.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the same objects when that database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261002_0002"
down_revision: str | None = "20261002_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tags, lineage mappings, and column last_seen_at when missing."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "columns" in tables:
        column_names: set[str] = {column["name"] for column in inspector.get_columns("columns")}
        if "last_seen_at" not in column_names:
            op.add_column(
                "columns",
                sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            )
    if "tags" not in tables:
        op.create_table(
            "tags",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
    if "table_tags" not in tables:
        op.create_table(
            "table_tags",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("table_id", sa.Uuid(), nullable=False),
            sa.Column("tag_id", sa.Uuid(), nullable=False),
            sa.Column("origin", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "origin IN ('USER', 'IMPORT')",
                name="ck_table_tags_origin",
            ),
            sa.ForeignKeyConstraint(["table_id"], ["catalog_tables.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("table_id", "tag_id", name="uq_table_tags_table_tag"),
        )
    if "marquez_mappings" not in tables:
        op.create_table(
            "marquez_mappings",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("catalog_table_id", sa.Uuid(), nullable=False),
            sa.Column("marquez_namespace", sa.String(length=255), nullable=False),
            sa.Column("marquez_dataset_name", sa.String(length=1024), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["catalog_table_id"],
                ["catalog_tables.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "marquez_namespace",
                "marquez_dataset_name",
                name="uq_marquez_mappings_dataset",
            ),
            sa.UniqueConstraint("catalog_table_id", name="uq_marquez_mappings_table"),
        )


def downgrade() -> None:
    """Drop the objects added by this revision."""
    bind: Connection = op.get_bind()
    inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "marquez_mappings" in tables:
        op.drop_table("marquez_mappings")
    if "table_tags" in tables:
        op.drop_table("table_tags")
    if "tags" in tables:
        op.drop_table("tags")
    if "columns" in tables:
        column_names: set[str] = {column["name"] for column in inspector.get_columns("columns")}
        if "last_seen_at" in column_names:
            op.drop_column("columns", "last_seen_at")
