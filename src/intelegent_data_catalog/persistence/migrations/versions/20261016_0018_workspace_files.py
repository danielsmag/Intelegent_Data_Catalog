"""Workspace folder and file tree. Bytes stay in object storage.

Revision 0001 creates the current models on a fresh database. This revision
adds the node table when the database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261016_0018"
down_revision: str | None = "20261016_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_CHECK: str = "kind IN ('FOLDER', 'FILE')"
_SIZE_CHECK: str = "size_bytes IS NULL OR size_bytes >= 0"


def upgrade() -> None:
    """Add the workspace node table."""
    bind: Connection = op.get_bind()
    _create_nodes(bind)


def downgrade() -> None:
    """Drop the workspace node table."""
    bind: Connection = op.get_bind()
    inspector: Inspector = inspect(bind)
    if "workspace_nodes" in set(inspector.get_table_names()):
        op.drop_table("workspace_nodes")


def _create_nodes(bind: Connection) -> None:
    inspector: Inspector = inspect(bind)
    if "workspace_nodes" in set(inspector.get_table_names()):
        return
    op.create_table(
        "workspace_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_KIND_CHECK, name="ck_workspace_nodes_kind"),
        sa.CheckConstraint(_SIZE_CHECK, name="ck_workspace_nodes_size"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["workspace_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "path", name="uq_workspace_nodes_path"),
    )
    op.create_index(
        "ix_workspace_nodes_parent",
        "workspace_nodes",
        ["workspace_id", "parent_id"],
    )
