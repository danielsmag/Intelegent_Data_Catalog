"""Reasoning effort on entity configuration and sync schedules.

Revision 0001 creates whatever the models contain on a fresh database.
This revision adds the column when that database was created earlier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261014_0015"
down_revision: str | None = "20261014_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EFFORT_CHECK: str = "effort IN ('off', 'minimal', 'low', 'medium', 'high', 'xhigh')"
_TABLES: tuple[tuple[str, str], ...] = (
    ("entity_configurations", "ck_entity_configurations_effort"),
    ("sync_schedules", "ck_sync_schedules_effort"),
)


def upgrade() -> None:
    """Add the reasoning effort used by context builds."""
    bind: Connection = op.get_bind()
    table: str
    constraint: str
    for table, constraint in _TABLES:
        _add_effort(bind, table, constraint)


def downgrade() -> None:
    """Drop the reasoning effort."""
    bind: Connection = op.get_bind()
    table: str
    constraint: str
    for table, constraint in _TABLES:
        _drop_effort(bind, table, constraint)


def _add_effort(bind: Connection, table: str, constraint: str) -> None:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns(table)}
    checks: set[str] = _check_names(table)
    if "effort" in present and constraint in checks:
        return
    with op.batch_alter_table(table) as batch:
        if "effort" not in present:
            batch.add_column(
                sa.Column(
                    "effort",
                    sa.String(length=16),
                    nullable=False,
                    server_default=sa.text("'off'"),
                ),
            )
        if constraint not in checks:
            batch.create_check_constraint(constraint, _EFFORT_CHECK)


def _drop_effort(bind: Connection, table: str, constraint: str) -> None:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns(table)}
    checks: set[str] = _check_names(table)
    if "effort" not in present and constraint not in checks:
        return
    with op.batch_alter_table(table) as batch:
        if constraint in checks:
            batch.drop_constraint(constraint, type_="check")
        if "effort" in present:
            batch.drop_column("effort")


def _check_names(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    names: set[str] = set()
    check_name: str | None
    for check_name in (item.get("name") for item in inspector.get_check_constraints(table)):
        if check_name is not None:
            names.add(check_name)
    return names
