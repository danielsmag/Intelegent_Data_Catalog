"""Definition DSL, S3 scan, lock, and the definition file API."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.assets.definitions.models import (
    DefinitionSpec,
    PageSourceRef,
    TableSourceRef,
)
from intelegent_data_catalog.assets.definitions.scanner import AssetDefinitionScanner
from intelegent_data_catalog.assets.dsl import (
    Asset,
    IcebergTableOperator,
    PostgresTableOperator,
    Space,
    TableOperator,
)
from intelegent_data_catalog.assets.dsl.collect import collect_definitions
from intelegent_data_catalog.assets.dsl.errors import (
    AssetDefinitionError,
    AssetDefinitionImportError,
)
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import AssetDefinitionsSettings
from intelegent_data_catalog.jobs.runtime import set_job_container
from intelegent_data_catalog.jobs.tasks import run_locked_scan
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    AirflowInstanceStatus,
    AssetKind,
    ContextOrigin,
    DataSourceType,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    TableKind,
    TableStatus,
    WorkspaceNodeKind,
)
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AirflowInstanceRow,
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
    AssetMembershipRow,
    AssetRow,
    CatalogTableRow,
    DataSourceRow,
    ScopeRow,
    SpaceRow,
    UserRow,
    WorkspaceNodeRow,
)
from intelegent_data_catalog.storage.object_store import MemoryObjectStore

_PREFIX: str = "asset-definitions/"
_SPACE: str = (
    "from intelegent_data_catalog.assets.dsl import Space\n"
    'Space("analytics", name="Analytics", description="Analytics domain", '
    'context="Sales analytics.")\n'
)
_SCOPE: str = (
    "from intelegent_data_catalog.assets.dsl import Scope\n"
    'Scope("finance", space="analytics", description="Finance data")\n'
)
_ORDERS: str = (
    "from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator\n"
    "Asset(\n"
    '    "orders",\n'
    '    IcebergTableOperator(source_key="iceberg.sales.orders", data_source="lakehouse"),\n'
    '    scope="analytics/finance",\n'
    '    name="Orders",\n'
    '    context="Fact table of orders.",\n'
    ")\n"
)
_TWO_TABLES: str = (
    _ORDERS + "Asset(\n"
    '    "returns",\n'
    '    IcebergTableOperator(source_key="iceberg.sales.returns", data_source="lakehouse"),\n'
    '    scope="analytics/finance",\n'
    ")\n"
)


def test_collect_each_kind() -> None:
    """A spaces file, a scopes file, and an assets file each yield their own spec."""
    spaces: DefinitionSpec = collect_definitions(
        _SPACE,
        "space_analytics.py",
        DefinitionKind.SPACE,
        timeout_seconds=5,
    )
    assert spaces.spaces[0].slug == "analytics"
    assert spaces.spaces[0].set_fields == ["name", "description", "context"]
    scopes: DefinitionSpec = collect_definitions(
        _SCOPE,
        "scope_finance.py",
        DefinitionKind.SCOPE,
        timeout_seconds=5,
    )
    assert scopes.scopes[0].space_slug == "analytics"
    assets: DefinitionSpec = collect_definitions(
        _ORDERS,
        "asset_orders.py",
        DefinitionKind.ASSET,
        timeout_seconds=5,
    )
    assert assets.assets[0].key == "orders"
    assert assets.assets[0].scope == "analytics/finance"


def test_iceberg_and_postgres_operators_share_table_fields() -> None:
    """Concrete table operators build a table source. The base stays abstract."""
    iceberg: IcebergTableOperator = IcebergTableOperator(
        source_key="iceberg.sales.orders",
        data_source="lakehouse",
    )
    postgres: PostgresTableOperator = PostgresTableOperator(
        source_key="app.public.customers",
        data_source="app",
    )
    iceberg_source: TableSourceRef = iceberg.to_source()
    postgres_source: TableSourceRef = postgres.to_source()
    assert iceberg_source.source_key == "iceberg.sales.orders"
    assert iceberg_source.data_source == "lakehouse"
    assert postgres_source.source_key == "app.public.customers"
    assert postgres_source.data_source == "app"
    with pytest.raises(TypeError):
        TableOperator(source_key="iceberg.sales.orders")
    with pytest.raises(ValidationError, match="data_source cannot be blank"):
        IcebergTableOperator(source_key="iceberg.sales.orders", data_source=" ")


def test_page_operator_defaults_workspace_to_shared() -> None:
    """PageOperator without a workspace names the shared workspace."""
    source: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, PageOperator\n"
        'Asset("readme", PageOperator(path="/docs/sales.md"), space="analytics")\n'
    )
    spec: DefinitionSpec = collect_definitions(
        source,
        "asset_readme.py",
        DefinitionKind.ASSET,
        timeout_seconds=5,
    )
    assert isinstance(spec.assets[0].source, PageSourceRef)
    assert spec.assets[0].source.workspace == "shared"


def test_wrong_kind_duplicate_target_and_syntax_fail_the_file() -> None:
    """Folder kind, duplicate keys, missing target, and syntax errors are import errors."""
    with pytest.raises(AssetDefinitionImportError, match="cannot declare"):
        collect_definitions(_SPACE, "asset_bad.py", DefinitionKind.ASSET, timeout_seconds=5)
    duplicated: str = _SPACE + 'Space("analytics")\n'
    with pytest.raises(AssetDefinitionImportError, match="duplicate key"):
        collect_definitions(duplicated, "space_dup.py", DefinitionKind.SPACE, timeout_seconds=5)
    both: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator\n"
        'Asset("orders", IcebergTableOperator(source_key="a.b.c"), '
        'scope="analytics/finance", space="analytics")\n'
    )
    with pytest.raises(AssetDefinitionImportError, match="exactly one"):
        collect_definitions(both, "asset_both.py", DefinitionKind.ASSET, timeout_seconds=5)
    neither: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator\n"
        'Asset("orders", IcebergTableOperator(source_key="a.b.c"))\n'
    )
    with pytest.raises(AssetDefinitionImportError, match="exactly one"):
        collect_definitions(neither, "asset_neither.py", DefinitionKind.ASSET, timeout_seconds=5)
    with pytest.raises(AssetDefinitionImportError, match="syntax error"):
        collect_definitions("def (\n", "asset_syntax.py", DefinitionKind.ASSET, timeout_seconds=5)


def test_definition_outside_a_file_raises() -> None:
    """Space and Asset register only while a file is being collected."""
    with pytest.raises(AssetDefinitionError, match="collected"):
        Space("analytics")
    with pytest.raises(AssetDefinitionError, match="collected"):
        Asset("orders", _table_operator(), space="analytics")


def test_collect_times_out() -> None:
    """A file that does not finish becomes an import error."""
    source: str = "import time\ntime.sleep(2)\n"
    with pytest.raises(AssetDefinitionImportError, match="timed out"):
        collect_definitions(source, "asset_slow.py", DefinitionKind.ASSET, timeout_seconds=0.05)


def test_scan_creates_space_scope_and_asset_in_one_tick(tmp_path: Path) -> None:
    """Nested asset keys are found, and spaces are applied before scopes and assets."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "assets/a/b/asset_orders.py", _ORDERS)
        _put(store, "scopes/analytics/scope_finance.py", _SCOPE)
        _put(store, "spaces/space_analytics.py", _SPACE)
        _put(store, "assets/readme.txt", "ignore")
        _put(store, "notes/asset_other.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        space: SpaceRow | None = session.scalar(
            select(SpaceRow).where(SpaceRow.slug == "analytics"),
        )
        assert space is not None
        assert space.description == "Analytics domain"
        assert space.context == "Sales analytics."
        assert space.context_origin == ContextOrigin.USER.value
        scope: ScopeRow | None = session.scalar(
            select(ScopeRow).where(ScopeRow.space_id == space.id, ScopeRow.slug == "finance"),
        )
        assert scope is not None
        asset: AssetRow | None = session.scalar(select(AssetRow).where(AssetRow.name == "Orders"))
        assert asset is not None
        assert asset.kind == AssetKind.TABLE.value
        assert asset.context == "Fact table of orders."
        assert asset.context_origin == ContextOrigin.USER.value
        membership: AssetMembershipRow | None = session.scalar(
            select(AssetMembershipRow).where(AssetMembershipRow.asset_id == asset.id),
        )
        assert membership is not None
        assert membership.scope_id == scope.id
        keys: set[str] = set(session.scalars(select(AssetDefinitionFileRow.key)))
        assert f"{_PREFIX}notes/asset_other.py" not in keys
        assert f"{_PREFIX}assets/readme.txt" not in keys
    finally:
        session.close()
        database.dispose()


def test_same_etag_is_a_no_op_and_a_change_updates_and_detaches(tmp_path: Path) -> None:
    """Unchanged bytes leave catalog edits in place. A new file drops removed memberships."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _table(session, "iceberg.sales.returns")
        _put(store, "spaces/space_analytics.py", _SPACE)
        _put(store, "scopes/analytics/scope_finance.py", _SCOPE)
        _put(store, "assets/sales/asset_orders.py", _TWO_TABLES)
        scanner.scan(datetime.now(UTC))
        space: SpaceRow = _space(session)
        space.description = "hacked"
        session.commit()
        scanner.scan(datetime.now(UTC))
        session.refresh(space)
        assert space.description == "hacked"
        updated: str = _SPACE.replace("Analytics domain", "Updated domain")
        _put(store, "spaces/space_analytics.py", updated)
        _put(store, "assets/sales/asset_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        session.refresh(space)
        assert space.description == "Updated domain"
        names: set[str] = set(session.scalars(select(AssetRow.name)))
        assert "Orders" in names
        memberships: list[AssetMembershipRow] = list(session.scalars(select(AssetMembershipRow)))
        assert len(memberships) == 1
    finally:
        session.close()
        database.dispose()


def test_deleted_key_detaches_membership_and_keeps_space(tmp_path: Path) -> None:
    """A key missing from the listing retires its membership. The space row stays."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "spaces/space_analytics.py", _SPACE)
        _put(store, "scopes/analytics/scope_finance.py", _SCOPE)
        asset_key: str = f"{_PREFIX}assets/sales/asset_orders.py"
        _put(store, "assets/sales/asset_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        store.delete(asset_key)
        scanner.scan(datetime.now(UTC))
        removed: AssetDefinitionFileRow | None = session.scalar(
            select(AssetDefinitionFileRow).where(AssetDefinitionFileRow.key == asset_key),
        )
        assert removed is not None
        assert removed.status == DefinitionFileStatus.REMOVED.value
        assert session.scalar(select(AssetMembershipRow)) is None
        assert _space(session).slug == "analytics"
        assert session.scalar(select(AssetRow).where(AssetRow.name == "Orders")) is not None
    finally:
        session.close()
        database.dispose()


def test_unresolved_table_applies_on_the_next_scan(tmp_path: Path) -> None:
    """A missing table stays unresolved until the catalog row exists, without re-executing."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner = _open(tmp_path)
    try:
        _put(store, "spaces/space_analytics.py", _SPACE)
        _put(store, "scopes/analytics/scope_finance.py", _SCOPE)
        _put(store, "assets/sales/asset_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        pending: AssetDefinitionItemRow | None = session.scalar(
            select(AssetDefinitionItemRow).where(AssetDefinitionItemRow.item_key == "orders"),
        )
        assert pending is not None
        assert pending.status == DefinitionItemStatus.UNRESOLVED.value
        _table(session, "iceberg.sales.orders")
        scanner.scan(datetime.now(UTC))
        session.refresh(pending)
        assert pending.status == DefinitionItemStatus.APPLIED.value
        assert pending.asset_id is not None
    finally:
        session.close()
        database.dispose()


def test_scope_before_its_space_resolves_on_retry(tmp_path: Path) -> None:
    """A scope uploaded before its space is applied on the scan that first sees the space."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner = _open(tmp_path)
    try:
        _put(store, "scopes/analytics/scope_finance.py", _SCOPE)
        scanner.scan(datetime.now(UTC))
        item: AssetDefinitionItemRow | None = session.scalar(select(AssetDefinitionItemRow))
        assert item is not None
        assert item.status == DefinitionItemStatus.UNRESOLVED.value
        _put(store, "spaces/space_analytics.py", _SPACE)
        scanner.scan(datetime.now(UTC))
        session.refresh(item)
        assert item.status == DefinitionItemStatus.APPLIED.value
        assert session.scalar(select(ScopeRow).where(ScopeRow.slug == "finance")) is not None
    finally:
        session.close()
        database.dispose()


def test_bad_name_and_bad_file_do_not_block_a_good_file(tmp_path: Path) -> None:
    """A misnamed Python file is an error row and is not executed. A syntax error stays local."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner = _open(tmp_path)
    try:
        _put(store, "assets/orders.py", _ORDERS)
        _put(store, "assets/asset_bad.py", "def (\n")
        _put(store, "spaces/space_analytics.py", _SPACE)
        scanner.scan(datetime.now(UTC))
        assert _space(session).slug == "analytics"
        misnamed: AssetDefinitionFileRow | None = session.scalar(
            select(AssetDefinitionFileRow).where(
                AssetDefinitionFileRow.key == f"{_PREFIX}assets/orders.py",
            ),
        )
        assert misnamed is not None
        assert misnamed.status == DefinitionFileStatus.ERROR.value
        assert misnamed.error is not None
        assert "asset_" in misnamed.error
        assert (
            session.scalar(
                select(AssetDefinitionItemRow).where(
                    AssetDefinitionItemRow.file_id == misnamed.id,
                ),
            )
            is None
        )
        broken: AssetDefinitionFileRow | None = session.scalar(
            select(AssetDefinitionFileRow).where(
                AssetDefinitionFileRow.key == f"{_PREFIX}assets/asset_bad.py",
            ),
        )
        assert broken is not None
        assert broken.status == DefinitionFileStatus.ERROR.value
    finally:
        session.close()
        database.dispose()


def test_airflow_and_page_operators_link_existing_rows(tmp_path: Path) -> None:
    """AirflowOperator and PageOperator attach the synced DAG and the workspace file."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner = _open(tmp_path)
    try:
        _dag(session)
        _node(session, "/docs/sales.md")
        _put(store, "spaces/space_analytics.py", _SPACE)
        source: str = (
            "from intelegent_data_catalog.assets.dsl import Asset, AirflowOperator, PageOperator\n"
            'Asset("etl", AirflowOperator(instance="prod-airflow", dag_id="etl_orders"), '
            'space="analytics")\n'
            'Asset("readme", PageOperator(path="/docs/sales.md"), space="analytics")\n'
        )
        _put(store, "assets/sales/asset_links.py", source)
        scanner.scan(datetime.now(UTC))
        kinds: set[str] = set(session.scalars(select(AssetRow.kind)))
        assert kinds == {AssetKind.AIRFLOW_DAG.value, AssetKind.FILE.value}
        assert len(list(session.scalars(select(AssetMembershipRow)))) == 2
    finally:
        session.close()
        database.dispose()


def test_held_lock_skips_scan() -> None:
    """A held lock does not start a scan and is not released."""

    class _HeldLock:
        def acquire(self, key: str, ttl_seconds: int) -> bool:
            del key, ttl_seconds
            return False

        def release(self, key: str) -> None:
            del key
            raise AssertionError("release")

    assert run_locked_scan(_HeldLock(), 30) == 0


def test_locked_scan_applies_space_and_releases(tmp_path: Path) -> None:
    """The locked runner scans the configured store and releases the lock."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    store: MemoryObjectStore = MemoryObjectStore()
    _put(store, "spaces/space_analytics.py", _SPACE)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.asset_definition_store.override(providers.Object(store))
    set_job_container(container)

    class _OpenLock:
        def __init__(self) -> None:
            self.released: bool = False

        def acquire(self, key: str, ttl_seconds: int) -> bool:
            del key, ttl_seconds
            return True

        def release(self, key: str) -> None:
            del key
            self.released = True

    lock: _OpenLock = _OpenLock()
    try:
        counted: int = run_locked_scan(lock, 30)
        assert counted == 1
        assert lock.released
        session: Session = database.session()
        try:
            assert session.scalar(select(SpaceRow).where(SpaceRow.slug == "analytics")) is not None
        finally:
            session.close()
    finally:
        database.dispose()


def test_api_lists_files_and_queues_a_scan(tmp_path: Path) -> None:
    """GET returns the scan rows. POST /scan answers 202."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    store: MemoryObjectStore = MemoryObjectStore()
    session: Session = database.session()
    settings: AssetDefinitionsSettings = AssetDefinitionsSettings(enabled=True, prefix=_PREFIX)
    scanner: AssetDefinitionScanner = AssetDefinitionScanner(
        session,
        store,
        settings,
        logging.getLogger("test.asset_definitions"),
    )
    try:
        _put(store, "spaces/space_analytics.py", _SPACE)
        scanner.scan(datetime.now(UTC))
    finally:
        session.close()
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.asset_definition_store.override(providers.Object(store))
    app: FastAPI = create_app(container)
    try:
        listed: httpx.Response = _send(app, "GET", "/api/v1/asset-definitions/files?folder=spaces")
        assert listed.status_code == 200
        rows: list[object] = listed.json()
        assert len(rows) == 1
        assert isinstance(rows[0], dict)
        file_id: str = str(rows[0]["id"])
        assert rows[0]["status"] == "OK"
        detail: httpx.Response = _send(app, "GET", f"/api/v1/asset-definitions/files/{file_id}")
        assert detail.status_code == 200
        body: dict[str, object] = detail.json()
        items: object = body["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        empty: httpx.Response = _send(app, "GET", "/api/v1/asset-definitions/files?folder=assets")
        assert empty.json() == []
        queued: httpx.Response = _send(app, "POST", "/api/v1/asset-definitions/scan")
        assert queued.status_code == 202
        assert queued.json()["queued"] is True
    finally:
        database.dispose()


def _table_operator() -> IcebergTableOperator:
    operator: IcebergTableOperator = IcebergTableOperator(source_key="iceberg.sales.orders")
    return operator


def _open(
    tmp_path: Path,
) -> tuple[CatalogDatabase, Session, MemoryObjectStore, AssetDefinitionScanner]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    session: Session = database.session()
    store: MemoryObjectStore = MemoryObjectStore()
    settings: AssetDefinitionsSettings = AssetDefinitionsSettings(enabled=True, prefix=_PREFIX)
    scanner: AssetDefinitionScanner = AssetDefinitionScanner(
        session,
        store,
        settings,
        logging.getLogger("test.asset_definitions"),
    )
    return database, session, store, scanner


def _put(store: MemoryObjectStore, key: str, source: str) -> None:
    store.put(f"{_PREFIX}{key}", source.encode(), "text/x-python")


def _space(session: Session) -> SpaceRow:
    found: SpaceRow | None = session.scalar(select(SpaceRow).where(SpaceRow.slug == "analytics"))
    assert found is not None
    return found


def _table(session: Session, source_key: str) -> None:
    now: datetime = datetime.now(UTC)
    source: DataSourceRow | None = session.scalar(
        select(DataSourceRow).where(DataSourceRow.name == "lakehouse"),
    )
    if source is None:
        source = DataSourceRow(
            id=uuid4(),
            name="lakehouse",
            type=DataSourceType.POSTGRES.value,
            configuration={},
            created_at=now,
            updated_at=now,
        )
        session.add(source)
        session.flush()
    table_name: str = source_key.rsplit(".", 1)[-1]
    session.add(
        CatalogTableRow(
            id=uuid4(),
            data_source_id=source.id,
            name=table_name,
            table_type=TableKind.TABLE.value,
            status=TableStatus.ACTIVE.value,
            physical_catalog="iceberg",
            physical_namespace="sales",
            physical_table=table_name,
            source_ref={},
            source_key=source_key,
            created_at=now,
            updated_at=now,
        ),
    )
    session.commit()


def _dag(session: Session) -> None:
    now: datetime = datetime.now(UTC)
    instance: AirflowInstanceRow = AirflowInstanceRow(
        id=uuid4(),
        name="Prod",
        slug="prod-airflow",
        base_url="http://airflow",
        username="airflow",
        password="secret",
        verify_tls=False,
        status=AirflowInstanceStatus.ACTIVE.value,
        created_at=now,
        updated_at=now,
    )
    session.add(instance)
    session.flush()
    session.add(
        AirflowDagRow(
            id=uuid4(),
            instance_id=instance.id,
            dag_id="etl_orders",
            is_paused=False,
            is_stale=False,
            has_import_errors=False,
            owners=[],
            tags=[],
            tasks=[],
            run_stats=[],
            last_seen_at=now,
        ),
    )
    session.commit()


def _node(session: Session, path: str) -> None:
    now: datetime = datetime.now(UTC)
    user: UserRow = UserRow(
        id=uuid4(),
        subject=f"sub-{uuid4()}",
        display_name="Dev",
        last_login_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    session.add(
        WorkspaceNodeRow(
            id=uuid4(),
            workspace_id=SHARED_WORKSPACE_ID,
            created_by=user.id,
            kind=WorkspaceNodeKind.FILE.value,
            name="sales.md",
            path=path,
            created_at=now,
            updated_at=now,
        ),
    )
    session.commit()


def _send(app: FastAPI, method: str, path: str) -> httpx.Response:
    return asyncio.run(_request(app, method, path))


async def _request(app: FastAPI, method: str, path: str) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path)
