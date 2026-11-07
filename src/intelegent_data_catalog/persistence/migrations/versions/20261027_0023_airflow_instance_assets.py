"""Link each Airflow instance to an AIRFLOW_INSTANCE asset.

Revision ID: 20261027_0023
Revises: 20261023_0022
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection, CursorResult

from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_SLUG

revision: str = "20261027_0023"
down_revision: str | None = "20261023_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_CHECK: str = (
    "kind IN ('TABLE', 'AIRFLOW_DAG', 'AIRFLOW_INSTANCE', 'NOTEBOOK', 'FILE')"
)
_KIND_CHECK_PREVIOUS: str = "kind IN ('TABLE', 'AIRFLOW_DAG', 'NOTEBOOK', 'FILE')"
_KIND_CONSTRAINT: str = "ck_assets_kind"
_ASSET_INDEX: str = "uq_airflow_instances_asset"


def upgrade() -> None:
    """Allow AIRFLOW_INSTANCE assets and backfill one per existing instance."""
    bind: Connection = op.get_bind()
    if "assets" in _tables(bind) and not _check_contains(
        bind,
        "assets",
        _KIND_CONSTRAINT,
        "AIRFLOW_INSTANCE",
    ):
        _replace_check("assets", _KIND_CONSTRAINT, _KIND_CHECK)
    if "airflow_instances" not in _tables(bind):
        return
    present: set[str] = _column_names(bind, "airflow_instances")
    if "agent_id" in present:
        return
    if "asset_id" not in present:
        with op.batch_alter_table("airflow_instances") as batch:
            batch.add_column(sa.Column("asset_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_airflow_instances_asset",
                "assets",
                ["asset_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if _ASSET_INDEX not in _index_names(bind, "airflow_instances"):
        op.create_index(
            _ASSET_INDEX,
            "airflow_instances",
            ["asset_id"],
            unique=True,
            sqlite_where=sa.text("asset_id IS NOT NULL"),
            postgresql_where=sa.text("asset_id IS NOT NULL"),
        )
    _backfill_instances(bind)


def downgrade() -> None:
    """Drop instance asset links and the AIRFLOW_INSTANCE kind."""
    bind: Connection = op.get_bind()
    if "airflow_instances" in _tables(bind) and "asset_id" in _column_names(
        bind,
        "airflow_instances",
    ):
        bind.execute(sa.text("UPDATE airflow_instances SET asset_id = NULL"))
    if "assets" in _tables(bind):
        bind.execute(sa.text("DELETE FROM assets WHERE kind = 'AIRFLOW_INSTANCE'"))
        if _check_contains(bind, "assets", _KIND_CONSTRAINT, "AIRFLOW_INSTANCE"):
            _replace_check("assets", _KIND_CONSTRAINT, _KIND_CHECK_PREVIOUS)
    if "airflow_instances" not in _tables(bind):
        return
    if _ASSET_INDEX in _index_names(bind, "airflow_instances"):
        op.drop_index(_ASSET_INDEX, table_name="airflow_instances")
    if "asset_id" in _column_names(bind, "airflow_instances"):
        fk_name: str | None = _asset_fk_name(bind, "airflow_instances")
        with op.batch_alter_table("airflow_instances") as batch:
            if fk_name is not None:
                batch.drop_constraint(fk_name, type_="foreignkey")
            batch.drop_column("asset_id")


def _backfill_instances(bind: Connection) -> None:
    if "assets" not in _tables(bind):
        return
    if "asset_id" not in _column_names(bind, "airflow_instances"):
        return
    workspace_id: uuid.UUID | None = _shared_workspace_id(bind)
    if workspace_id is None:
        return
    used: set[tuple[str, str]] = _used_slugs(bind)
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT id, name, slug, description, context, context_origin, status, "
            "last_synced_at, created_at, updated_at "
            "FROM airflow_instances WHERE asset_id IS NULL"
        )
    )
    now: datetime = datetime.now(UTC)
    record: tuple[object, ...]
    for record in rows:
        asset_id: uuid.UUID = uuid.uuid4()
        instance_id: uuid.UUID = _as_uuid(record[0])
        name: str = str(record[1])
        slug: str = str(record[2])
        created_at: datetime = _moment(record[8]) if record[8] is not None else now
        updated_at: datetime = _moment(record[9]) if record[9] is not None else created_at
        status: str = "DEPRECATED" if str(record[6]) == "DISABLED" else "ACTIVE"
        _insert_asset(
            bind,
            asset_id=asset_id,
            kind="AIRFLOW_INSTANCE",
            name=_clip(name, 255),
            slug=_claim_slug(used, workspace_id, slug or name),
            description=_optional_text(record[3]),
            context=_optional_text(record[4]),
            context_origin=_optional_text(record[5]),
            workspace_id=workspace_id,
            properties={"instance_id": str(instance_id), "slug": slug},
            status=status,
            last_synced_at=_optional_moment(record[7]),
            created_at=created_at,
            updated_at=updated_at,
        )
        bind.execute(
            sa.text(
                "UPDATE airflow_instances SET asset_id = :asset_id WHERE id = :id"
            ).bindparams(
                sa.bindparam("asset_id", type_=sa.Uuid()),
                sa.bindparam("id", type_=sa.Uuid()),
            ),
            {"asset_id": asset_id, "id": instance_id},
        )


def _insert_asset(
    bind: Connection,
    *,
    asset_id: uuid.UUID,
    kind: str,
    name: str,
    slug: str,
    description: str | None,
    context: str | None,
    context_origin: str | None,
    workspace_id: uuid.UUID,
    properties: dict[str, object],
    status: str,
    last_synced_at: datetime | None,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    bind.execute(
        sa.text(
            "INSERT INTO assets ("
            "id, kind, name, slug, description, context, context_origin, workspace_id, "
            "owner_user_id, created_by, properties, status, last_synced_at, created_at, updated_at"
            ") VALUES ("
            ":id, :kind, :name, :slug, :description, :context, :context_origin, :workspace_id, "
            "NULL, NULL, :properties, :status, :last_synced_at, :created_at, :updated_at)"
        ).bindparams(
            sa.bindparam("id", type_=sa.Uuid()),
            sa.bindparam("workspace_id", type_=sa.Uuid()),
            sa.bindparam("properties", type_=sa.JSON()),
            sa.bindparam("last_synced_at", type_=sa.DateTime(timezone=True)),
            sa.bindparam("created_at", type_=sa.DateTime(timezone=True)),
            sa.bindparam("updated_at", type_=sa.DateTime(timezone=True)),
        ),
        {
            "id": asset_id,
            "kind": kind,
            "name": name,
            "slug": slug,
            "description": description,
            "context": context,
            "context_origin": context_origin,
            "workspace_id": workspace_id,
            "properties": properties,
            "status": status,
            "last_synced_at": last_synced_at,
            "created_at": created_at,
            "updated_at": updated_at,
        },
    )


def _used_slugs(bind: Connection) -> set[tuple[str, str]]:
    rows: CursorResult[tuple[object, ...]] = bind.execute(
        sa.text(
            "SELECT workspace_id, slug FROM assets WHERE owner_user_id IS NULL"
        )
    )
    used: set[tuple[str, str]] = set()
    record: tuple[object, ...]
    for record in rows:
        used.add((str(_as_uuid(record[0])), str(record[1])))
    return used


def _claim_slug(
    used: set[tuple[str, str]],
    workspace_id: uuid.UUID,
    raw: str,
) -> str:
    bucket: str = str(workspace_id)
    cleaned: str = "".join(char.lower() if char.isalnum() else "-" for char in raw).strip("-")
    if cleaned == "":
        cleaned = "asset"
    cleaned = cleaned[:200]
    candidate: str = cleaned
    number: int = 2
    while (bucket, candidate) in used:
        suffix: str = f"-{number}"
        candidate = f"{cleaned[: 200 - len(suffix)]}{suffix}"
        number += 1
    used.add((bucket, candidate))
    return candidate


def _shared_workspace_id(bind: Connection) -> uuid.UUID | None:
    if "workspaces" not in _tables(bind):
        return None
    found: object = bind.execute(
        sa.text("SELECT id FROM workspaces WHERE slug = :slug"),
        {"slug": SHARED_WORKSPACE_SLUG},
    ).scalar()
    if found is None:
        return None
    return _as_uuid(found)


def _replace_check(table: str, name: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def _check_contains(bind: Connection, table: str, name: str, token: str) -> bool:
    inspector: Inspector = inspect(bind)
    check: dict[str, object]
    for check in inspector.get_check_constraints(table):
        if check.get("name") != name:
            continue
        sqltext: object = check.get("sqltext")
        return isinstance(sqltext, str) and token in sqltext
    return False


def _tables(bind: Connection) -> set[str]:
    inspector: Inspector = inspect(bind)
    return set(inspector.get_table_names())


def _column_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    return {str(column["name"]) for column in inspector.get_columns(table)}


def _index_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    found: set[str] = set()
    for item in inspector.get_indexes(table):
        label: object = item.get("name")
        if isinstance(label, str):
            found.add(label)
    return found


def _asset_fk_name(bind: Connection, table: str) -> str | None:
    inspector: Inspector = inspect(bind)
    item: dict[str, object]
    for item in inspector.get_foreign_keys(table):
        columns: object = item.get("constrained_columns")
        if columns != ["asset_id"]:
            continue
        label: object = item.get("name")
        if isinstance(label, str) and label != "":
            return label
    return None


def _as_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _moment(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("timestamp missing")


def _optional_moment(value: object) -> datetime | None:
    if value is None:
        return None
    return _moment(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]
