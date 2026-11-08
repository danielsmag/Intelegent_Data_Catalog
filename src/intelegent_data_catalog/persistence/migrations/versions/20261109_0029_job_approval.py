"""Allow a catalog job to wait for approval.

Revision 0001 creates the current models, which already allow the status.
This revision widens a check written by an earlier jobs migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261109_0029"
down_revision: str | None = "20261109_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE: str = "catalog_jobs"
_CONSTRAINT: str = "ck_catalog_jobs_status"
_STATUS: str = "waiting_approval"
_WITH_APPROVAL: str = "status IN ('running', 'succeeded', 'failed', 'waiting_approval')"
_WITHOUT_APPROVAL: str = "status IN ('running', 'succeeded', 'failed')"
_INDEX: str = "uq_catalog_jobs_running"
_INDEX_SQL: str = (
    "CREATE UNIQUE INDEX uq_catalog_jobs_running ON catalog_jobs (kind, subject_id, lane) "
    "WHERE status IN ('running', 'waiting_approval')"
)
_OLD_INDEX_SQL: str = (
    "CREATE UNIQUE INDEX uq_catalog_jobs_running ON catalog_jobs (kind, subject_id, lane) "
    "WHERE status = 'running'"
)


def upgrade() -> None:
    """Allow waiting_approval and keep that row in the one-active-job index."""
    bind: Connection = op.get_bind()
    if _TABLE not in set(inspect(bind).get_table_names()):
        return
    if _check_sql(bind) is not None and not _check_contains(bind, _STATUS):
        _replace_check(_WITH_APPROVAL)
    _recreate_index(_INDEX_SQL)


def downgrade() -> None:
    """Return paused jobs to failed and restore the previous status check."""
    bind: Connection = op.get_bind()
    if _TABLE not in set(inspect(bind).get_table_names()):
        return
    bind.exec_driver_sql(
        f"UPDATE {_TABLE} SET status = 'failed', error = 'approval removed' "
        f"WHERE status = '{_STATUS}'"
    )
    if _check_sql(bind) is not None and _check_contains(bind, _STATUS):
        _replace_check(_WITHOUT_APPROVAL)
    _recreate_index(_OLD_INDEX_SQL)


def _recreate_index(statement: str) -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX}"))
    op.execute(sa.text(statement))


def _replace_check(expression: str) -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, expression)


def _check_contains(bind: Connection, token: str) -> bool:
    sqltext: str | None = _check_sql(bind)
    return sqltext is not None and token in sqltext


def _check_sql(bind: Connection) -> str | None:
    inspector: Inspector = inspect(bind)
    for check in inspector.get_check_constraints(_TABLE):
        if check.get("name") != _CONSTRAINT:
            continue
        sqltext: object = check.get("sqltext")
        if isinstance(sqltext, str):
            return sqltext
        return None
    return None
