"""Asset migration backfills tables, DAGs, and files."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from intelegent_data_catalog.persistence import migrate as migrate_module
from intelegent_data_catalog.persistence.migrate import upgrade_database


def test_upgrade_from_workspace_files_backfills_assets(tmp_path: Path) -> None:
    """Revision 0019 copies a table, a DAG attachment, and a file into assets."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    config: Config = Config()
    script: Path = Path(migrate_module.__file__).resolve().parent / "migrations"
    config.set_main_option("script_location", str(script))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "20261016_0018")
    engine: Engine = create_engine(url)
    now: str = datetime.now(UTC).isoformat()
    space: str = uuid4().hex
    scope: str = uuid4().hex
    source: str = uuid4().hex
    table: str = uuid4().hex
    instance: str = uuid4().hex
    dag: str = uuid4().hex
    user: str = uuid4().hex
    node: str = uuid4().hex
    workspace: str = "00000000000040008000000000000001"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, subject, display_name, last_login_at, "
                "created_at, updated_at) "
                "VALUES (:id, 'sub', 'U', :t, :t, :t)"
            ),
            {"id": user, "t": now},
        )
        connection.execute(
            text(
                "INSERT INTO spaces (id, name, slug, isolation_mode, created_at, updated_at) "
                "VALUES (:id, 'S', 's', 'OPEN', :t, :t)"
            ),
            {"id": space, "t": now},
        )
        connection.execute(
            text(
                "INSERT INTO scopes (id, space_id, name, slug, created_at, updated_at) "
                "VALUES (:id, :sid, 'sc', 'sc', :t, :t)"
            ),
            {"id": scope, "sid": space, "t": now},
        )
        connection.execute(
            text(
                "INSERT INTO data_sources (id, name, type, configuration, created_at, updated_at) "
                "VALUES (:id, 'ds', 'POSTGRES', '{}', :t, :t)"
            ),
            {"id": source, "t": now},
        )
        connection.execute(
            text(
                "INSERT INTO catalog_tables (id, scope_id, data_source_id, name, "
                "table_type, status, "
                "physical_catalog, physical_namespace, physical_table, source_ref, source_key, "
                "created_at, updated_at) VALUES (:id, :scope, :src, 'orders', 'TABLE', 'ACTIVE', "
                "'c', 'n', 'orders', '{}', 'c.n.orders', :t, :t)"
            ),
            {"id": table, "scope": scope, "src": source, "t": now},
        )
        connection.execute(
            text(
                "INSERT INTO airflow_instances (id, name, slug, base_url, username, password, "
                "verify_tls, status, created_at, updated_at) "
                "VALUES (:id, 'af', 'af', 'http://a', 'u', 'p', 1, 'ACTIVE', :t, :t)"
            ),
            {"id": instance, "t": now},
        )
        connection.execute(
            text(
                "INSERT INTO airflow_dags (id, instance_id, dag_id, is_paused, is_stale, "
                "has_import_errors, owners, tags, tasks, run_stats, last_seen_at) "
                "VALUES (:id, :inst, 'daily', 0, 0, 0, '[]', '[]', '[]', '[]', :t)"
            ),
            {"id": dag, "inst": instance, "t": now},
        )
        connection.execute(
            text(
                "INSERT INTO airflow_attachments (id, dag_row_id, scope_id, created_at) "
                "VALUES (:id, :dag, :scope, :t)"
            ),
            {"id": uuid4().hex, "dag": dag, "scope": scope, "t": now},
        )
        connection.execute(
            text(
                "INSERT INTO workspace_nodes (id, workspace_id, created_by, kind, name, path, "
                "created_at, updated_at) VALUES (:id, :ws, :user, 'FILE', 'note.md', "
                "'Shared/note.md', :t, :t)"
            ),
            {"id": node, "ws": workspace, "user": user, "t": now},
        )
    command.upgrade(config, "20261027_0023")
    with engine.connect() as connection:
        kind_rows = connection.execute(text("SELECT kind FROM assets"))
        kinds: set[str] = {str(row[0]) for row in kind_rows}
        assert kinds == {"TABLE", "AIRFLOW_DAG", "AIRFLOW_INSTANCE", "FILE"}
        counted = connection.execute(text("SELECT COUNT(*) FROM asset_memberships"))
        memberships: int = int(counted.scalar_one())
        assert memberships == 2
        dag_links: int = int(
            connection.execute(
                text("SELECT COUNT(*) FROM airflow_attachments WHERE dag_row_id IS NOT NULL"),
            ).scalar_one(),
        )
        assert dag_links == 0
        nullable: bool = bool(
            next(
                column
                for column in __import__("sqlalchemy").inspect(engine).get_columns("catalog_tables")
                if column["name"] == "scope_id"
            )["nullable"],
        )
        assert nullable is True
    engine.dispose()
