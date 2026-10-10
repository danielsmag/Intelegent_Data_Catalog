"""Lower the sync interval floor to 1 second.

The configuration field accepts any minute value, including 0.2.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision: str = "20261013_0010"
down_revision: str | None = "20261013_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME: str = "ck_sync_schedules_interval"
_FLOOR: int = 1


def upgrade() -> None:
    """Allow a 1 second sync interval."""
    bind: Connection = op.get_bind()
    _set_floor(bind, _FLOOR)


def downgrade() -> None:
    """Restore the 30 second floor. Shorter rows move up to 30."""
    bind: Connection = op.get_bind()
    if not _has_table(bind):
        return
    op.execute(
        sa.text("UPDATE sync_schedules SET interval_seconds = 30 WHERE interval_seconds < 30"),
    )
    _set_floor(bind, 30)


def _set_floor(bind: Connection, floor: int) -> None:
    if not _has_table(bind):
        return
    current: int | None = _floor(bind)
    if current == floor:
        return
    with op.batch_alter_table("sync_schedules") as batch:
        if current is not None:
            batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, f"interval_seconds >= {floor}")


def _has_table(bind: Connection) -> bool:
    names: set[str] = set(inspect(bind).get_table_names())
    return "sync_schedules" in names


def _floor(bind: Connection) -> int | None:
    checks: list[dict[str, object]] = list(inspect(bind).get_check_constraints("sync_schedules"))
    check: dict[str, object]
    for check in checks:
        if check.get("name") != _NAME:
            continue
        sqltext: object = check.get("sqltext")
        if not isinstance(sqltext, str):
            return None
        match: re.Match[str] | None = re.search(r">=\s*(\d+)", sqltext)
        if match is None:
            return None
        return int(match.group(1))
    return None
