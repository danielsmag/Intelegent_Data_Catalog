"""Drop the space and scope hierarchy.

Catalog tables stay on their data source. Assets stay on their workspace.
Rows that only pointed at a space or a scope are deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.operations.ops import BatchOperations
from sqlalchemy import Inspector, inspect
from sqlalchemy.engine import Connection

revision: str = "20261028_0024"
down_revision: str | None = "20261027_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_CONSTRAINT: str = "ck_catalog_jobs_kind"
_JOB_WITHOUT_HIERARCHY: str = "kind IN ('knowledge', 'context', 'airflow', 'asset')"
_JOB_WITH_HIERARCHY: str = "kind IN ('knowledge', 'scope', 'space', 'context', 'airflow', 'asset')"
_CONFIG_CONSTRAINT: str = "ck_entity_configurations_one_subject"
_CONFIG_WITHOUT_HIERARCHY: str = (
    "(catalog_table_id IS NOT NULL AND asset_id IS NULL) OR "
    "(catalog_table_id IS NULL AND asset_id IS NOT NULL)"
)
_CONFIG_WITH_HIERARCHY: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL AND catalog_table_id IS NULL "
    "AND asset_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NOT NULL AND catalog_table_id IS NULL "
    "AND asset_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NULL AND catalog_table_id IS NOT NULL "
    "AND asset_id IS NULL) OR "
    "(space_id IS NULL AND scope_id IS NULL AND catalog_table_id IS NULL "
    "AND asset_id IS NOT NULL)"
)
_FILE_KIND_CONSTRAINT: str = "ck_asset_definition_files_kind"
_ITEM_KIND_CONSTRAINT: str = "ck_asset_definition_items_kind"
_KIND_ASSET: str = "kind IN ('ASSET')"
_FOLDER_ASSET: str = "folder_kind IN ('ASSET')"
_KIND_HIERARCHY: str = "kind IN ('SPACE', 'SCOPE', 'ASSET')"
_FOLDER_HIERARCHY: str = "folder_kind IN ('SPACE', 'SCOPE', 'ASSET')"
_ORIGIN_CHECK: str = "context_origin IN ('USER', 'LLM')"
_ISOLATION_CHECK: str = "isolation_mode IN ('OPEN', 'ISOLATED')"
_ONE_MEMBERSHIP: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL) OR (space_id IS NULL AND scope_id IS NOT NULL)"
)
_ONE_SOURCE: str = (
    "(airflow_instance_id IS NOT NULL AND dag_row_id IS NULL) OR "
    "(airflow_instance_id IS NULL AND dag_row_id IS NOT NULL)"
)
_ONE_TARGET: str = (
    "(space_id IS NOT NULL AND scope_id IS NULL) OR (space_id IS NULL AND scope_id IS NOT NULL)"
)
_DROP_TABLES: tuple[str, ...] = (
    "airflow_attachments",
    "asset_memberships",
    "workspace_space_bindings",
    "space_knowledge_contexts",
    "scope_knowledge_contexts",
    "scopes",
    "spaces",
)


def upgrade() -> None:
    """Delete hierarchy rows, then drop the tables and foreign keys."""
    bind: Connection = op.get_bind()
    _delete_jobs(bind)
    _delete_configurations(bind)
    _delete_definitions(bind)
    _shrink_checks(bind)
    _drop_columns(bind, "catalog_tables", ("scope_id",))
    _drop_columns(bind, "source_engine_bindings", ("default_scope_id",))
    _drop_columns(bind, "entity_configurations", ("space_id", "scope_id"))
    _drop_columns(bind, "asset_definition_items", ("space_id", "scope_id"))
    tables: set[str] = _tables(bind)
    name: str
    for name in _DROP_TABLES:
        if name in tables:
            op.drop_table(name)


def downgrade() -> None:
    """Recreate empty space and scope tables. Deleted rows stay gone."""
    bind: Connection = op.get_bind()
    _create_spaces(bind)
    _create_scopes(bind)
    _create_space_contexts(bind)
    _create_scope_contexts(bind)
    _create_bindings(bind)
    _create_memberships(bind)
    _create_attachments(bind)
    _add_scope_column(bind, "catalog_tables", "scope_id", "scopes")
    _add_scope_column(bind, "source_engine_bindings", "default_scope_id", "scopes")
    _add_configuration_subjects(bind)
    _add_definition_subjects(bind)
    _restore_checks(bind)


def _delete_jobs(bind: Connection) -> None:
    if "catalog_jobs" not in _tables(bind):
        return
    bind.execute(sa.text("DELETE FROM catalog_jobs WHERE kind IN ('space', 'scope')"))


def _delete_configurations(bind: Connection) -> None:
    if "entity_configurations" not in _tables(bind):
        return
    present: set[str] = _column_names(bind, "entity_configurations")
    if "space_id" not in present and "scope_id" not in present:
        return
    bind.execute(
        sa.text(
            "DELETE FROM entity_configurations "
            "WHERE space_id IS NOT NULL OR scope_id IS NOT NULL"
        )
    )


def _delete_definitions(bind: Connection) -> None:
    tables: set[str] = _tables(bind)
    if "asset_definition_items" in tables:
        bind.execute(
            sa.text("DELETE FROM asset_definition_items WHERE kind IN ('SPACE', 'SCOPE')")
        )
    if "asset_definition_files" in tables:
        bind.execute(
            sa.text(
                "DELETE FROM asset_definition_files WHERE folder_kind IN ('SPACE', 'SCOPE')"
            )
        )


def _shrink_checks(bind: Connection) -> None:
    if _check_contains(bind, "catalog_jobs", _JOB_CONSTRAINT, "'space'"):
        _replace_check("catalog_jobs", _JOB_CONSTRAINT, _JOB_WITHOUT_HIERARCHY)
    if _check_contains(bind, "entity_configurations", _CONFIG_CONSTRAINT, "space_id"):
        _replace_check("entity_configurations", _CONFIG_CONSTRAINT, _CONFIG_WITHOUT_HIERARCHY)
    if _check_contains(bind, "asset_definition_files", _FILE_KIND_CONSTRAINT, "'SPACE'"):
        _replace_check("asset_definition_files", _FILE_KIND_CONSTRAINT, _FOLDER_ASSET)
    if _check_contains(bind, "asset_definition_items", _ITEM_KIND_CONSTRAINT, "'SPACE'"):
        _replace_check("asset_definition_items", _ITEM_KIND_CONSTRAINT, _KIND_ASSET)


def _restore_checks(bind: Connection) -> None:
    if (
        "catalog_jobs" in _tables(bind)
        and _check_contains(bind, "catalog_jobs", _JOB_CONSTRAINT, "'asset'")
        and not _check_contains(bind, "catalog_jobs", _JOB_CONSTRAINT, "'space'")
    ):
        _replace_check("catalog_jobs", _JOB_CONSTRAINT, _JOB_WITH_HIERARCHY)
    config_sql: str | None = _check_sql(bind, "entity_configurations", _CONFIG_CONSTRAINT)
    has_space_column: bool = (
        "entity_configurations" in _tables(bind)
        and "space_id" in _column_names(bind, "entity_configurations")
    )
    if config_sql is not None and "space_id" not in config_sql and has_space_column:
        _replace_check("entity_configurations", _CONFIG_CONSTRAINT, _CONFIG_WITH_HIERARCHY)
    file_sql: str | None = _check_sql(bind, "asset_definition_files", _FILE_KIND_CONSTRAINT)
    if file_sql is not None and "'SPACE'" not in file_sql:
        _replace_check("asset_definition_files", _FILE_KIND_CONSTRAINT, _FOLDER_HIERARCHY)
    item_sql: str | None = _check_sql(bind, "asset_definition_items", _ITEM_KIND_CONSTRAINT)
    if item_sql is not None and "'SPACE'" not in item_sql:
        _replace_check("asset_definition_items", _ITEM_KIND_CONSTRAINT, _KIND_HIERARCHY)


def _drop_columns(bind: Connection, table: str, names: tuple[str, ...]) -> None:
    if table not in _tables(bind):
        return
    present: set[str] = _column_names(bind, table)
    targets: set[str] = {name for name in names if name in present}
    if len(targets) == 0:
        return
    inspector: Inspector = inspect(bind)
    with op.batch_alter_table(table) as batch:
        item: dict[str, object]
        for item in inspector.get_unique_constraints(table):
            _drop_named(batch, item, "column_names", targets, "unique")
        for item in inspector.get_foreign_keys(table):
            _drop_named(batch, item, "constrained_columns", targets, "foreignkey")
        for item in inspector.get_indexes(table):
            if item.get("unique") is True:
                continue
            _drop_named(batch, item, "column_names", targets, "index")
        name: str
        for name in names:
            if name in present:
                batch.drop_column(name)


def _drop_named(
    batch: BatchOperations,
    item: dict[str, object],
    column_key: str,
    targets: set[str],
    kind: str,
) -> None:
    label: object = item.get("name")
    columns: object = item.get(column_key)
    if not isinstance(label, str) or not isinstance(columns, list):
        return
    if targets.isdisjoint(columns):
        return
    if kind == "index":
        batch.drop_index(label)
        return
    batch.drop_constraint(label, type_=kind)


def _add_scope_column(bind: Connection, table: str, column: str, referred: str) -> None:
    if table not in _tables(bind) or referred not in _tables(bind):
        return
    if column in _column_names(bind, table):
        return
    with op.batch_alter_table(table) as batch:
        batch.add_column(sa.Column(column, sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            f"fk_{table}_{column}",
            referred,
            [column],
            ["id"],
            ondelete="RESTRICT",
        )


def _add_configuration_subjects(bind: Connection) -> None:
    if "entity_configurations" not in _tables(bind):
        return
    if "spaces" not in _tables(bind) or "scopes" not in _tables(bind):
        return
    present: set[str] = _column_names(bind, "entity_configurations")
    with op.batch_alter_table("entity_configurations") as batch:
        if "space_id" not in present:
            batch.add_column(sa.Column("space_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_entity_configurations_space",
                "spaces",
                ["space_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch.create_unique_constraint("uq_entity_configurations_space", ["space_id"])
        if "scope_id" not in present:
            batch.add_column(sa.Column("scope_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_entity_configurations_scope",
                "scopes",
                ["scope_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch.create_unique_constraint("uq_entity_configurations_scope", ["scope_id"])


def _add_definition_subjects(bind: Connection) -> None:
    if "asset_definition_items" not in _tables(bind):
        return
    if "spaces" not in _tables(bind) or "scopes" not in _tables(bind):
        return
    present: set[str] = _column_names(bind, "asset_definition_items")
    with op.batch_alter_table("asset_definition_items") as batch:
        if "space_id" not in present:
            batch.add_column(sa.Column("space_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_asset_definition_items_space",
                "spaces",
                ["space_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "scope_id" not in present:
            batch.add_column(sa.Column("scope_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_asset_definition_items_scope",
                "scopes",
                ["scope_id"],
                ["id"],
                ondelete="SET NULL",
            )


def _create_spaces(bind: Connection) -> None:
    if "spaces" in _tables(bind):
        return
    op.create_table(
        "spaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("isolation_mode", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("context_origin", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ISOLATION_CHECK, name="ck_spaces_isolation_mode"),
        sa.CheckConstraint(_ORIGIN_CHECK, name="ck_spaces_context_origin"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )


def _create_scopes(bind: Connection) -> None:
    if "scopes" in _tables(bind) or "spaces" not in _tables(bind):
        return
    op.create_table(
        "scopes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("context_origin", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ORIGIN_CHECK, name="ck_scopes_context_origin"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", "slug", name="uq_scopes_space_slug"),
    )


def _create_space_contexts(bind: Connection) -> None:
    if "space_knowledge_contexts" in _tables(bind) or "spaces" not in _tables(bind):
        return
    op.create_table(
        "space_knowledge_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", name="uq_space_knowledge_contexts_space"),
    )


def _create_scope_contexts(bind: Connection) -> None:
    if "scope_knowledge_contexts" in _tables(bind) or "scopes" not in _tables(bind):
        return
    op.create_table(
        "scope_knowledge_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_id", name="uq_scope_knowledge_contexts_scope"),
    )


def _create_bindings(bind: Connection) -> None:
    if "workspace_space_bindings" in _tables(bind):
        return
    if "spaces" not in _tables(bind) or "workspaces" not in _tables(bind):
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


def _create_memberships(bind: Connection) -> None:
    if "asset_memberships" in _tables(bind):
        return
    if (
        "assets" not in _tables(bind)
        or "spaces" not in _tables(bind)
        or "scopes" not in _tables(bind)
    ):
        return
    op.create_table(
        "asset_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=True),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ONE_MEMBERSHIP, name="ck_asset_memberships_one_target"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_attachments(bind: Connection) -> None:
    if "airflow_attachments" in _tables(bind):
        return
    required: tuple[str, ...] = ("airflow_instances", "airflow_dags", "spaces", "scopes")
    if any(name not in _tables(bind) for name in required):
        return
    op.create_table(
        "airflow_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("airflow_instance_id", sa.Uuid(), nullable=True),
        sa.Column("dag_row_id", sa.Uuid(), nullable=True),
        sa.Column("space_id", sa.Uuid(), nullable=True),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ONE_SOURCE, name="ck_airflow_attachments_one_source"),
        sa.CheckConstraint(_ONE_TARGET, name="ck_airflow_attachments_one_target"),
        sa.ForeignKeyConstraint(
            ["airflow_instance_id"],
            ["airflow_instances.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["dag_row_id"], ["airflow_dags.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def _replace_check(table: str, name: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def _check_contains(bind: Connection, table: str, name: str, token: str) -> bool:
    sqltext: str | None = _check_sql(bind, table, name)
    return sqltext is not None and token in sqltext


def _check_sql(bind: Connection, table: str, name: str) -> str | None:
    if table not in _tables(bind):
        return None
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


def _tables(bind: Connection) -> set[str]:
    inspector: Inspector = inspect(bind)
    return set(inspector.get_table_names())


def _column_names(bind: Connection, table: str) -> set[str]:
    inspector: Inspector = inspect(bind)
    return {str(column["name"]) for column in inspector.get_columns(table)}
