"""Workspaces, users, memberships, space bindings, and space isolation.

Revision 0001 creates the current models on a fresh database. This revision
adds the same tables when the database was created earlier, then seeds Shared.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import (
    SHARED_WORKSPACE_ID,
    SHARED_WORKSPACE_NAME,
    SHARED_WORKSPACE_SLUG,
)

revision: str = "20261016_0017"
down_revision: str | None = "20261014_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ISOLATION_CHECK: str = "isolation_mode IN ('OPEN', 'ISOLATED')"
_ROLE_CHECK: str = "role IN ('ADMIN', 'MEMBER')"


def upgrade() -> None:
    """Add workspace tables and the shared workspace row."""
    bind: Connection = op.get_bind()
    _add_isolation_mode(bind)
    _create_workspaces(bind)
    _create_users(bind)
    _create_members(bind)
    _create_bindings(bind)
    _seed_shared(bind)


def downgrade() -> None:
    """Drop workspace tables and the isolation column."""
    bind: Connection = op.get_bind()
    inspector: Inspector = inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "workspace_space_bindings" in tables:
        op.drop_table("workspace_space_bindings")
    if "workspace_members" in tables:
        op.drop_table("workspace_members")
    if "users" in tables:
        op.drop_table("users")
    if "workspaces" in tables:
        op.drop_table("workspaces")
    _drop_isolation_mode(bind)


def _add_isolation_mode(bind: Connection) -> None:
    inspector: Inspector = inspect(bind)
    if "spaces" not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns("spaces")}
    if "isolation_mode" in present:
        return
    with op.batch_alter_table("spaces") as batch:
        batch.add_column(
            sa.Column(
                "isolation_mode",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'OPEN'"),
            )
        )
        batch.create_check_constraint("ck_spaces_isolation_mode", _ISOLATION_CHECK)


def _drop_isolation_mode(bind: Connection) -> None:
    inspector: Inspector = inspect(bind)
    if "spaces" not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns("spaces")}
    if "isolation_mode" not in present:
        return
    with op.batch_alter_table("spaces") as batch:
        batch.drop_constraint("ck_spaces_isolation_mode", type_="check")
        batch.drop_column("isolation_mode")


def _create_workspaces(bind: Connection) -> None:
    inspector: Inspector = inspect(bind)
    if "workspaces" in set(inspector.get_table_names()):
        return
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )


def _create_users(bind: Connection) -> None:
    inspector: Inspector = inspect(bind)
    if "users" in set(inspector.get_table_names()):
        return
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject"),
        sa.UniqueConstraint("email"),
    )


def _create_members(bind: Connection) -> None:
    inspector: Inspector = inspect(bind)
    if "workspace_members" in set(inspector.get_table_names()):
        return
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ROLE_CHECK, name="ck_workspace_members_role"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_pair"),
    )


def _create_bindings(bind: Connection) -> None:
    inspector: Inspector = inspect(bind)
    if "workspace_space_bindings" in set(inspector.get_table_names()):
        return
    op.create_table(
        "workspace_space_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "space_id", name="uq_workspace_space_bindings_pair"),
    )


def _seed_shared(bind: Connection) -> None:
    inspector: Inspector = inspect(bind)
    if "workspaces" not in set(inspector.get_table_names()):
        return
    now: datetime = utc_now()
    shared_id: uuid.UUID = SHARED_WORKSPACE_ID
    stored_id: str = shared_id.hex if bind.dialect.name == "sqlite" else str(shared_id)
    stamp: str = now.isoformat()
    bind.execute(
        sa.text(
            "INSERT INTO workspaces "
            "(id, name, slug, description, is_system, created_at, updated_at) "
            "SELECT :id, :name, :slug, :description, true, :created_at, :updated_at "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM workspaces WHERE id = :match_id OR slug = :match_slug)"
        ),
        {
            "id": stored_id,
            "match_id": stored_id,
            "name": SHARED_WORKSPACE_NAME,
            "slug": SHARED_WORKSPACE_SLUG,
            "match_slug": SHARED_WORKSPACE_SLUG,
            "description": "Workspace every user can see",
            "created_at": stamp,
            "updated_at": stamp,
        },
    )
