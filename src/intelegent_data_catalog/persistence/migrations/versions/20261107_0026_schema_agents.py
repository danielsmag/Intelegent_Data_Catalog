"""Allow SCHEMA agents.

Revision 0001 creates the current models, which already allow SCHEMA.
This revision widens an older check.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261107_0026"
down_revision: str | None = "20261107_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT: str = "ck_agents_kind"
_CONSTRAINT_OLD: str = "ck_assets_kind"
_WITH_SCHEMA: str = (
    "kind IN ('TABLE', 'AIRFLOW_DAG', 'AIRFLOW_INSTANCE', 'NOTEBOOK', 'FILE', 'SCHEMA')"
)
_WITHOUT_SCHEMA: str = (
    "kind IN ('TABLE', 'AIRFLOW_DAG', 'AIRFLOW_INSTANCE', 'NOTEBOOK', 'FILE')"
)


def upgrade() -> None:
    """Allow SCHEMA on the agents kind check."""
    bind: Connection = op.get_bind()
    table: str | None = _agents_table(bind)
    if table is None:
        return
    name: str | None = _kind_constraint(bind, table)
    if name is None or _check_contains(bind, table, name, "SCHEMA"):
        return
    _replace_check(table, name, _WITH_SCHEMA)


def downgrade() -> None:
    """Remove SCHEMA agents and restore the previous kind check."""
    bind: Connection = op.get_bind()
    table: str | None = _agents_table(bind)
    if table is None:
        return
    bind.exec_driver_sql(f"DELETE FROM {table} WHERE kind = 'SCHEMA'")
    name: str | None = _kind_constraint(bind, table)
    if name is None or not _check_contains(bind, table, name, "SCHEMA"):
        return
    _replace_check(table, name, _WITHOUT_SCHEMA)


def _agents_table(bind: Connection) -> str | None:
    tables: set[str] = set(inspect(bind).get_table_names())
    if "agents" in tables:
        return "agents"
    if "assets" in tables:
        return "assets"
    return None


def _kind_constraint(bind: Connection, table: str) -> str | None:
    if _check_sql(bind, table, _CONSTRAINT) is not None:
        return _CONSTRAINT
    if _check_sql(bind, table, _CONSTRAINT_OLD) is not None:
        return _CONSTRAINT_OLD
    return None


def _replace_check(table: str, name: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def _check_contains(bind: Connection, table: str, name: str, token: str) -> bool:
    sqltext: str | None = _check_sql(bind, table, name)
    return sqltext is not None and token in sqltext


def _check_sql(bind: Connection, table: str, name: str) -> str | None:
    inspector: Inspector = inspect(bind)
    check: dict[str, object]
    for check in inspector.get_check_constraints(table):
        if check.get("name") != name:
            continue
        sqltext: object = check.get("sqltext")
        if isinstance(sqltext, str):
            return sqltext
        return None
    return None
