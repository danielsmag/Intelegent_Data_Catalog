"""Kernel environment is built from registry rows, not hardcoded connections."""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.core.settings import (
    AppSettings,
    CredentialEntry,
    NotebookSettings,
    SeedPostgresSettings,
    TrinoSettings,
    load_settings,
)
from intelegent_data_catalog.notebooks.credentials import Credential, EnvCredentialResolver
from intelegent_data_catalog.notebooks.kernel_env import KernelEnvBuilder, KernelEnvironment
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    DataSourceType,
    EngineConnectionStatus,
    EngineType,
)
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import (
    DataSourceRow,
    EngineConnectionRow,
    SourceEngineBindingRow,
)

_PASSWORD: str = "s3cr3t-reader"


def test_trino_and_postgres_bindings_become_kernel_env(tmp_path: Path) -> None:
    """Active Trino rows and Postgres bindings are exported. Secrets stay out of warnings."""
    database: CatalogDatabase
    session: Session
    database, session = _database(tmp_path)
    try:
        warehouse: EngineConnectionRow = _engine("warehouse", "http://trino:8080")
        session.add(warehouse)
        disabled: EngineConnectionRow = _engine(
            "old",
            "http://trino:8080",
            status=EngineConnectionStatus.DISABLED.value,
        )
        session.add(disabled)
        sales: DataSourceRow = _postgres("sales", "source_db", "postgres", "sales")
        crm: DataSourceRow = _postgres("crm", "missing-ref", "crm-db", "crm")
        session.add(sales)
        session.add(crm)
        session.add(_binding(sales, warehouse, "appdb"))
        session.add(_binding(crm, warehouse, "crm"))
        session.commit()
        environment: KernelEnvironment = _builder(session).build(_access())
    finally:
        session.close()
        database.dispose()
    assert environment.engines == ("warehouse",)
    assert environment.spark_catalogs == ("appdb",)
    assert "s3cr3t-reader" not in " ".join(environment.warnings)
    catalogs: object = json.loads(environment.variables["CATALOG_SPARK_CATALOGS_JSON"])
    assert isinstance(catalogs, list)
    encoded: str = json.dumps(catalogs)
    assert _PASSWORD in encoded
    assert environment.variables["CATALOG_USER"] == "dev"
    engines: object = json.loads(environment.variables["CATALOG_ENGINES_JSON"])
    assert engines == [{"name": "warehouse", "host": "trino", "port": 8080, "default": True}]


def test_missing_credential_is_a_warning(tmp_path: Path) -> None:
    """One bad source does not drop the sources that resolved."""
    database: CatalogDatabase
    session: Session
    database, session = _database(tmp_path)
    try:
        warehouse: EngineConnectionRow = _engine("warehouse", "http://trino:8080")
        broken: DataSourceRow = _postgres("billing", None, "billing", "billing")
        session.add(warehouse)
        session.add(broken)
        session.add(_binding(broken, warehouse, "billing"))
        session.commit()
        environment: KernelEnvironment = _builder(session).build(_access())
    finally:
        session.close()
        database.dispose()
    assert environment.spark_catalogs == ()
    assert any("billing" in warning for warning in environment.warnings)
    payload: object = json.loads(environment.variables["CATALOG_SPARK_CATALOGS_JSON"])
    assert isinstance(payload, list)
    assert payload[0]["error"]


def test_duplicate_binding_keys_get_a_suffix(tmp_path: Path) -> None:
    """Two connections can share a binding key. The second catalog name is suffixed."""
    database: CatalogDatabase
    session: Session
    database, session = _database(tmp_path)
    try:
        first: EngineConnectionRow = _engine("one", "http://trino:8080")
        second: EngineConnectionRow = _engine("two", "http://trino:8080")
        sales: DataSourceRow = _postgres("sales", "source_db", "postgres", "sales")
        other: DataSourceRow = _postgres("other", "source_db", "other-db", "other")
        session.add_all([first, second, sales, other])
        session.add(_binding(sales, first, "appdb"))
        session.add(_binding(other, second, "appdb"))
        session.commit()
        environment: KernelEnvironment = _builder(session).build(_access())
    finally:
        session.close()
        database.dispose()
    assert set(environment.spark_catalogs) == {"appdb", "appdb_2"}
    assert any("appdb_2" in warning for warning in environment.warnings)


def test_trino_settings_fallback_when_registry_is_empty(tmp_path: Path) -> None:
    """Host Trino settings are the dev fallback, not a second source of truth."""
    database: CatalogDatabase
    session: Session
    database, session = _database(tmp_path)
    try:
        settings: AppSettings = load_settings().model_copy(
            update={"trino": TrinoSettings(enabled=True, host="trino", port=8080, user="catalog")}
        )
        environment: KernelEnvironment = KernelEnvBuilder(
            session,
            settings,
            EnvCredentialResolver({}),
        ).build(_access())
    finally:
        session.close()
        database.dispose()
    assert environment.engines == ("trino",)


def test_seed_postgres_when_registry_has_no_catalog(tmp_path: Path) -> None:
    """Compose can expose the local sales database before a registry row exists."""
    database: CatalogDatabase
    session: Session
    database, session = _database(tmp_path)
    try:
        settings: AppSettings = load_settings().model_copy(
            update={
                "notebook": NotebookSettings(
                    enabled=True,
                    seed_postgres=SeedPostgresSettings(enabled=True, credential_ref="source_db"),
                )
            }
        )
        environment: KernelEnvironment = KernelEnvBuilder(
            session,
            settings,
            _resolver(),
        ).build(_access())
    finally:
        session.close()
        database.dispose()
    assert environment.spark_catalogs == ("appdb",)


def test_env_prefix_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """CATALOG_CREDENTIALS__<REF>__USER is enough when settings were not given the dict."""
    monkeypatch.setenv("CATALOG_CREDENTIALS__SOURCE_DB__USER", "sales_reader")
    monkeypatch.setenv("CATALOG_CREDENTIALS__SOURCE_DB__PASSWORD", _PASSWORD)
    found: Credential | None = EnvCredentialResolver({}).resolve("source_db")
    assert found is not None
    assert found.user == "sales_reader"
    assert found.password == _PASSWORD


def _builder(session: Session) -> KernelEnvBuilder:
    return KernelEnvBuilder(session, load_settings(), _resolver())


def _resolver() -> EnvCredentialResolver:
    return EnvCredentialResolver(
        {"source_db": CredentialEntry(user="sales_reader", password=SecretStr(_PASSWORD))}
    )


def _access() -> AccessContext:
    return AccessContext(
        principal=Principal("dev", None, "Dev", frozenset(), True),
        user_id=UUID("00000000-0000-4000-8000-000000000010"),
        workspace_id=SHARED_WORKSPACE_ID,
        member_workspace_ids=frozenset({SHARED_WORKSPACE_ID}),
        admin_workspace_ids=frozenset({SHARED_WORKSPACE_ID}),
    )


def _database(tmp_path: Path) -> tuple[CatalogDatabase, Session]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    return database, database.session()


def _engine(
    name: str,
    endpoint: str,
    status: str = EngineConnectionStatus.ACTIVE.value,
) -> EngineConnectionRow:
    now = utc_now()
    return EngineConnectionRow(
        id=uuid4(),
        name=name,
        engine_type=EngineType.TRINO.value,
        endpoint=endpoint,
        credential_ref=None,
        configuration={},
        status=status,
        created_at=now,
        updated_at=now,
    )


def _postgres(name: str, credential_ref: str | None, host: str, database: str) -> DataSourceRow:
    now = utc_now()
    return DataSourceRow(
        id=uuid4(),
        name=name,
        type=DataSourceType.POSTGRES.value,
        configuration={
            "host": host,
            "port": 5432,
            "database": database,
            "username": "sales_reader",
        },
        credential_ref=credential_ref,
        created_at=now,
        updated_at=now,
    )


def _binding(
    source: DataSourceRow,
    engine: EngineConnectionRow,
    binding_key: str,
) -> SourceEngineBindingRow:
    now = utc_now()
    return SourceEngineBindingRow(
        id=uuid4(),
        data_source_id=source.id,
        engine_connection_id=engine.id,
        binding_key=binding_key,
        engine_type=engine.engine_type,
        configuration={},
        capabilities={},
        created_at=now,
        updated_at=now,
    )
