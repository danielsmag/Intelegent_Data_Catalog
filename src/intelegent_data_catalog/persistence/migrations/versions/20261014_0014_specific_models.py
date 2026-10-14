"""Store a specific model id instead of the local/gpt provider key.

Revision 0001 creates whatever the models contain on a fresh database.
This revision rewrites provider keys and replaces the check when an earlier
database still allows only local and gpt.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261014_0014"
down_revision: str | None = "20261014_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODEL_CHECK: str = (
    "model IN ('llama3.2', 'qwen3:4b', 'gpt-4.1', 'gpt-4.1-mini', 'gpt-6-astra')"
)
_LEGACY_CHECK: str = "model IN ('local', 'gpt')"
_TABLES: tuple[tuple[str, str], ...] = (
    ("entity_configurations", "ck_entity_configurations_model"),
    ("sync_schedules", "ck_sync_schedules_model"),
)


def upgrade() -> None:
    """Rewrite provider keys and allow the catalog model ids."""
    bind: Connection = op.get_bind()
    table: str
    constraint: str
    for table, constraint in _TABLES:
        _upgrade_table(bind, table, constraint)


def downgrade() -> None:
    """Collapse catalog ids back to local and gpt."""
    bind: Connection = op.get_bind()
    table: str
    constraint: str
    for table, constraint in _TABLES:
        _downgrade_table(bind, table, constraint)


def _upgrade_table(bind: Connection, table: str, constraint: str) -> None:
    if not _has_model(bind, table):
        return
    op.execute(sa.text(f"UPDATE {table} SET model = 'llama3.2' WHERE model = 'local'"))
    op.execute(sa.text(f"UPDATE {table} SET model = 'gpt-4.1' WHERE model = 'gpt'"))
    _replace_check(table, constraint, _MODEL_CHECK)


def _downgrade_table(bind: Connection, table: str, constraint: str) -> None:
    if not _has_model(bind, table):
        return
    op.execute(
        sa.text(
            f"UPDATE {table} SET model = 'local' WHERE model IN ('llama3.2', 'qwen3:4b')",
        ),
    )
    op.execute(
        sa.text(
            f"UPDATE {table} SET model = 'gpt' "
            "WHERE model IN ('gpt-4.1', 'gpt-4.1-mini', 'gpt-6-astra')",
        ),
    )
    _replace_check(table, constraint, _LEGACY_CHECK)


def _has_model(bind: Connection, table: str) -> bool:
    inspector = inspect(bind)
    if table not in set(inspector.get_table_names()):
        return False
    present: set[str] = {column["name"] for column in inspector.get_columns(table)}
    return "model" in present


def _replace_check(table: str, constraint: str, expression: str) -> None:
    checks: set[str] = set()
    check_name: str | None
    inspector = inspect(op.get_bind())
    named: list[str | None] = [item.get("name") for item in inspector.get_check_constraints(table)]
    for check_name in named:
        if check_name is not None:
            checks.add(check_name)
    with op.batch_alter_table(table) as batch:
        if constraint in checks:
            batch.drop_constraint(constraint, type_="check")
        batch.create_check_constraint(constraint, expression)
