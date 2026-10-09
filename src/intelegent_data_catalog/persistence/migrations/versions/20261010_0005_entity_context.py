"""Business context text on scopes, tables, and columns.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the columns when that database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261010_0005"
down_revision: str | None = "20261009_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[tuple[str, str], ...] = (
    ("scopes", "ck_scopes_context_origin"),
    ("catalog_tables", "ck_catalog_tables_context_origin"),
    ("columns", "ck_columns_context_origin"),
)
_ORIGIN_CHECK: str = "context_origin IN ('USER', 'LLM')"


def upgrade() -> None:
    """Add nullable context text and its origin check."""
    bind: Connection = op.get_bind()
    table: str
    constraint: str
    for table, constraint in _TABLES:
        _add_context(bind, table, constraint)


def downgrade() -> None:
    """Drop the context columns and their origin checks."""
    bind: Connection = op.get_bind()
    table: str
    constraint: str
    for table, constraint in _TABLES:
        _drop_context(bind, table, constraint)


def _add_context(bind: Connection, table: str, constraint: str) -> None:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns(table)}
    if "context" in present and "context_origin" in present:
        return
    with op.batch_alter_table(table) as batch:
        if "context" not in present:
            batch.add_column(sa.Column("context", sa.Text(), nullable=True))
        if "context_origin" not in present:
            batch.add_column(sa.Column("context_origin", sa.String(length=32), nullable=True))
        batch.create_check_constraint(constraint, _ORIGIN_CHECK)


def _drop_context(bind: Connection, table: str, constraint: str) -> None:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns(table)}
    checks: set[str] = set()
    check_name: str | None
    for check_name in (item.get("name") for item in inspector.get_check_constraints(table)):
        if check_name is not None:
            checks.add(check_name)
    if "context" not in present and "context_origin" not in present and constraint not in checks:
        return
    with op.batch_alter_table(table) as batch:
        if constraint in checks:
            batch.drop_constraint(constraint, type_="check")
        if "context_origin" in present:
            batch.drop_column("context_origin")
        if "context" in present:
            batch.drop_column("context")
