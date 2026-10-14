"""Default new configuration rows to GPT-4.1 and effort low.

Changes the column default only. Rows already stored keep their model and effort.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261014_0016"
down_revision: str | None = "20261014_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("entity_configurations", "sync_schedules")


def upgrade() -> None:
    """Point omitted model and effort columns at GPT-4.1 and low."""
    bind: Connection = op.get_bind()
    table: str
    for table in _TABLES:
        _set_defaults(bind, table, model="'gpt-4.1'", effort="'low'")


def downgrade() -> None:
    """Restore the previous column defaults."""
    bind: Connection = op.get_bind()
    table: str
    for table in _TABLES:
        _set_defaults(bind, table, model="'llama3.2'", effort="'off'")


def _set_defaults(bind: Connection, table: str, model: str, effort: str) -> None:
    inspector: Inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    present: set[str] = {column["name"] for column in inspector.get_columns(table)}
    with op.batch_alter_table(table) as batch:
        if "model" in present:
            batch.alter_column(
                "model",
                existing_type=sa.String(length=16),
                server_default=sa.text(model),
            )
        if "effort" in present:
            batch.alter_column(
                "effort",
                existing_type=sa.String(length=16),
                server_default=sa.text(effort),
            )
