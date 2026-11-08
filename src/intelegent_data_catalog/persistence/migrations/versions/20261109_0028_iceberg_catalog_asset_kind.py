"""Allow ICEBERG_CATALOG assets.

Revision 0001 creates the current models, which already allow the kind.
This revision widens a check written by an earlier assets migration.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261109_0028"
down_revision: str | None = "20261108_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE: str = "assets"
_CONSTRAINT: str = "ck_assets_kind"
_KIND: str = "ICEBERG_CATALOG"
_WITH_CATALOG: str = (
    "kind IN ('AIRFLOW_INSTANCE', 'ICEBERG_TABLE', 'ICEBERG_SCHEMA', "
    "'ICEBERG_CATALOG', 'GITHUB_REPO')"
)
_WITHOUT_CATALOG: str = (
    "kind IN ('AIRFLOW_INSTANCE', 'ICEBERG_TABLE', 'ICEBERG_SCHEMA', 'GITHUB_REPO')"
)


def upgrade() -> None:
    """Allow ICEBERG_CATALOG on the assets kind check."""
    bind: Connection = op.get_bind()
    if _TABLE not in set(inspect(bind).get_table_names()):
        return
    if _check_sql(bind) is None or _check_contains(bind, _KIND):
        return
    _replace_check(_WITH_CATALOG)


def downgrade() -> None:
    """Remove catalog assets and restore the previous kind check."""
    bind: Connection = op.get_bind()
    if _TABLE not in set(inspect(bind).get_table_names()):
        return
    bind.exec_driver_sql(f"DELETE FROM {_TABLE} WHERE kind = '{_KIND}'")
    if _check_sql(bind) is None or not _check_contains(bind, _KIND):
        return
    _replace_check(_WITHOUT_CATALOG)


def _replace_check(expression: str) -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, expression)


def _check_contains(bind: Connection, token: str) -> bool:
    sqltext: str | None = _check_sql(bind)
    return sqltext is not None and token in sqltext


def _check_sql(bind: Connection) -> str | None:
    inspector: Inspector = inspect(bind)
    check: dict[str, object]
    for check in inspector.get_check_constraints(_TABLE):
        if check.get("name") != _CONSTRAINT:
            continue
        sqltext: object = check.get("sqltext")
        if isinstance(sqltext, str):
            return sqltext
        return None
    return None
