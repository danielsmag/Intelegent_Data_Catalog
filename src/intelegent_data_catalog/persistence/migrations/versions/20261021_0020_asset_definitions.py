"""Asset definition files scanned from object storage.

Revision 0001 creates the current models on a fresh database. This revision
adds the same tables when the database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261021_0020"
down_revision: str | None = "20261021_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_CHECK: str = "kind IN ('SPACE', 'SCOPE', 'ASSET')"
_FOLDER_CHECK: str = "folder_kind IN ('SPACE', 'SCOPE', 'ASSET')"
_FILE_STATUS_CHECK: str = "status IN ('OK', 'ERROR', 'REMOVED')"
_ITEM_STATUS_CHECK: str = "status IN ('APPLIED', 'UNRESOLVED', 'ERROR', 'REMOVED')"


def upgrade() -> None:
    """Add definition file and item tables when they are not already present."""
    bind: Connection = op.get_bind()
    tables: set[str] = _tables(bind)
    if "agent_definition_files" in tables or "agent_definition_items" in tables:
        return
    if "asset_definition_files" not in tables:
        _create_files()
    if "asset_definition_items" not in tables:
        _create_items()


def downgrade() -> None:
    """Drop definition item and file tables."""
    bind: Connection = op.get_bind()
    tables: set[str] = _tables(bind)
    if "asset_definition_items" in tables:
        op.drop_table("asset_definition_items")
    if "asset_definition_files" in tables:
        op.drop_table("asset_definition_files")


def _create_files() -> None:
    op.create_table(
        "asset_definition_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("folder_kind", sa.String(length=16), nullable=False),
        sa.Column("etag", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("last_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_FOLDER_CHECK, name="ck_asset_definition_files_kind"),
        sa.CheckConstraint(_FILE_STATUS_CHECK, name="ck_asset_definition_files_status"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_asset_definition_files_size"),
        sa.CheckConstraint("item_count >= 0", name="ck_asset_definition_files_items"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_asset_definition_files_key"),
    )
    op.create_index(
        "ix_asset_definition_files_status",
        "asset_definition_files",
        ["status"],
    )


def _create_items() -> None:
    op.create_table(
        "asset_definition_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("item_key", sa.String(length=512), nullable=False),
        sa.Column("declaration", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=True),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_KIND_CHECK, name="ck_asset_definition_items_kind"),
        sa.CheckConstraint(_ITEM_STATUS_CHECK, name="ck_asset_definition_items_status"),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["asset_definition_files.id"],
            name="fk_asset_definition_items_file",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name="fk_asset_definition_items_space",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scopes.id"],
            name="fk_asset_definition_items_scope",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_asset_definition_items_asset",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id", "item_key", name="uq_asset_definition_items_file_key"),
    )
    op.create_index("ix_asset_definition_items_status", "asset_definition_items", ["status"])
    op.create_index("ix_asset_definition_items_asset", "asset_definition_items", ["asset_id"])


def _tables(bind: Connection) -> set[str]:
    inspector: Inspector = inspect(bind)
    return set(inspector.get_table_names())
