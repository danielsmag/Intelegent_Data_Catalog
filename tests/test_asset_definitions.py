"""Definition DSL, S3 scan, lock, and the definition file API."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.assets.definitions.models import (
    DefinitionSpec,
    OperatorRef,
    PageSourceRef,
    TableSourceRef,
)
from intelegent_data_catalog.assets.definitions.scanner import AssetDefinitionScanner
from intelegent_data_catalog.assets.dsl import (
    AirflowInstanceOperator,
    AirflowOperator,
    Asset,
    IcebergTableOperator,
    PageOperator,
    PostgresTableOperator,
    Space,
    TableOperator,
)
from intelegent_data_catalog.assets.dsl.collect import collect_definitions
from intelegent_data_catalog.assets.dsl.errors import (
    AssetDefinitionError,
    AssetDefinitionImportError,
)
from intelegent_data_catalog.assets.dsl.operators.base import OperatorContext, OperatorObserver
from intelegent_data_catalog.assets.dsl.operators.registry import operator_from_ref
from intelegent_data_catalog.assets.execution.executor import (
    CeleryOperatorExecutor,
    LocalOperatorExecutor,
)
from intelegent_data_catalog.assets.execution.runtime import OperatorRunner
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.assets.registry import AssetRegistry
from intelegent_data_catalog.assets.service import _UnavailableAgents
from intelegent_data_catalog.assets.strategies.instance import InstanceAssetDetail
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import AssetDefinitionsSettings
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.runtime import set_job_container
from intelegent_data_catalog.jobs.tasks import run_locked_scan
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    AirflowInstanceStatus,
    AssetKind,
    AssetStatus,
    ContextOrigin,
    DataSourceType,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
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
from intelegent_data_catalog.schedual.cron import next_cron
from intelegent_data_catalog.schedual.operators import AssetOperatorSchedule
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
    '    IcebergTableOperator(source_key="iceberg.sales.orders", data_source="lakehouse", '
    "include_lineage=True),\n"
    '    schedule="0 2 * * *",\n'
    ")\n"
)
_TWO_TABLES: str = (
    _ORDERS + "Asset(\n"
    '    "returns",\n'
    '    IcebergTableOperator(source_key="iceberg.sales.returns", data_source="lakehouse"),\n'
    ")\n"
)


class _RecordingExecutor:
    """Remembers submitted workloads and does not run them."""

    def __init__(self) -> None:
        self.workloads: list[OperatorWorkload] = []

    def submit(self, workload: OperatorWorkload) -> None:
        self.workloads.append(workload)


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
    assert assets.assets[0].schedule == "0 2 * * *"
    assert assets.assets[0].operator is not None
    assert assets.assets[0].operator.type == "IcebergTableOperator"
    assert assets.assets[0].tags == []
    assert "tags" not in assets.assets[0].set_fields


def test_asset_accepts_keyword_fields_and_tags() -> None:
    """Tags are optional labels. Blanks and repeats are dropped. Order stays."""
    source: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator\n"
        "asset = Asset(\n"
        '    key="customer_spend",\n'
        '    description="Spend and order count per customer, built from customers and orders.",\n'
        '    context="Grain is one row per customer.",\n'
        "    operator=IcebergTableOperator(\n"
        '        source_key="polaris.sales.customer_spend",\n'
        '        data_source="Polaris",\n'
        "        include_lineage=True,\n"
        "    ),\n"
        '    properties={"owner": "commerce-analytics", "tier": "gold"},\n'
        '    tags=[" gold ", "customer", "gold"],\n'
        '    schedule="0 2 * * *",\n'
        ")\n"
    )
    spec: DefinitionSpec = collect_definitions(
        source,
        "asset_customer_spend.py",
        DefinitionKind.ASSET,
        timeout_seconds=5,
    )
    assert spec.assets[0].key == "customer_spend"
    assert spec.assets[0].name is None
    assert "name" not in spec.assets[0].set_fields
    assert spec.assets[0].tags == ["gold", "customer"]
    assert "tags" in spec.assets[0].set_fields
    assert spec.assets[0].properties == {"owner": "commerce-analytics", "tier": "gold"}


def test_bad_tags_fail_the_file() -> None:
    """A tag that is not a short label fails the definition file."""
    source: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator\n"
        'Asset("orders", IcebergTableOperator(source_key="a.b.c"), tags=["ok", "bad/tag"])\n'
    )
    with pytest.raises(AssetDefinitionImportError, match="each tag"):
        collect_definitions(source, "asset_tags.py", DefinitionKind.ASSET, timeout_seconds=5)


def test_scan_stores_declared_tags(tmp_path: Path) -> None:
    """A tags argument is stored on the asset and returned with the row."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, _executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        source: str = (
            "from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator\n"
            "Asset(\n"
            '    key="order_mart",\n'
            '    operator=IcebergTableOperator(source_key="iceberg.sales.orders", '
            'data_source="lakehouse"),\n'
            '    tags=["gold", "orders"],\n'
            ")\n"
        )
        _put(store, "assets/asset_orders.py", source)
        scanner.scan(datetime.now(UTC))
        asset: AssetRow | None = session.scalar(
            select(AssetRow).where(AssetRow.name == "order_mart"),
        )
        assert asset is not None
        assert asset.properties["tags"] == ["gold", "orders"]
    finally:
        session.close()
        database.dispose()


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
        'Asset("readme", PageOperator(path="/docs/sales.md"))\n'
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
    bad_cron: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator\n"
        'Asset("orders", IcebergTableOperator(source_key="a.b.c"), schedule="daily")\n'
    )
    with pytest.raises(AssetDefinitionImportError, match="five-field"):
        collect_definitions(bad_cron, "asset_cron.py", DefinitionKind.ASSET, timeout_seconds=5)
    with pytest.raises(TypeError):
        Asset("orders", _table_operator(), scope="analytics/finance")  # type: ignore[call-arg]
    with pytest.raises(AssetDefinitionImportError, match="syntax error"):
        collect_definitions("def (\n", "asset_syntax.py", DefinitionKind.ASSET, timeout_seconds=5)


def test_definition_outside_a_file_raises() -> None:
    """Space and Asset register only while a file is being collected."""
    with pytest.raises(AssetDefinitionError, match="collected"):
        Space("analytics")
    with pytest.raises(AssetDefinitionError, match="collected"):
        Asset("orders", _table_operator())


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
    database, session, store, scanner, executor = _open(tmp_path)
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
        asset: AssetRow | None = session.scalar(select(AssetRow).where(AssetRow.name == "orders"))
        assert asset is not None
        assert asset.kind == AssetKind.TABLE.value
        assert asset.name == "orders"
        assert asset.context is None
        assert session.scalar(select(AssetMembershipRow)) is None
        item: AssetDefinitionItemRow | None = session.scalar(
            select(AssetDefinitionItemRow).where(AssetDefinitionItemRow.item_key == "orders"),
        )
        assert item is not None
        assert item.run_status == DefinitionRunStatus.QUEUED.value
        assert item.schedule == "0 2 * * *"
        assert len(executor.workloads) == 1
        assert scope.id is not None
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
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _table(session, "iceberg.sales.returns")
        _put(store, "spaces/space_analytics.py", _SPACE)
        _put(store, "scopes/analytics/scope_finance.py", _SCOPE)
        _put(store, "assets/sales/asset_orders.py", _TWO_TABLES)
        scanner.scan(datetime.now(UTC))
        assert len(executor.workloads) == 2
        executor.workloads.clear()
        space: SpaceRow = _space(session)
        space.description = "hacked"
        session.commit()
        scanner.scan(datetime.now(UTC))
        session.refresh(space)
        assert executor.workloads == []
        assert space.description == "hacked"
        updated: str = _SPACE.replace("Analytics domain", "Updated domain")
        _put(store, "spaces/space_analytics.py", updated)
        _put(store, "assets/sales/asset_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        session.refresh(space)
        assert space.description == "Updated domain"
        names: set[str] = set(session.scalars(select(AssetRow.name)))
        assert names == {"orders"}
        assert session.scalar(select(AssetMembershipRow)) is None
        assert executor.workloads == []
    finally:
        session.close()
        database.dispose()


def test_deleted_script_removes_the_asset_and_keeps_the_space(tmp_path: Path) -> None:
    """A key missing from the listing deletes its asset. The space row stays."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "spaces/space_analytics.py", _SPACE)
        _put(store, "scopes/analytics/scope_finance.py", _SCOPE)
        asset_key: str = f"{_PREFIX}assets/sales/asset_orders.py"
        _put(store, "assets/sales/asset_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        assert len(executor.workloads) == 1
        store.delete(asset_key)
        scanner.scan(datetime.now(UTC))
        removed: AssetDefinitionFileRow | None = session.scalar(
            select(AssetDefinitionFileRow).where(AssetDefinitionFileRow.key == asset_key),
        )
        assert removed is not None
        assert removed.status == DefinitionFileStatus.REMOVED.value
        assert session.scalar(select(AssetMembershipRow)) is None
        assert _space(session).slug == "analytics"
        assert session.scalar(select(AssetRow).where(AssetRow.name == "orders")) is None
    finally:
        session.close()
        database.dispose()


def test_scan_drops_assets_no_script_declares(tmp_path: Path) -> None:
    """An asset that no asset script names is deleted on the next scan."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, _executor = _open(tmp_path)
    try:
        now: datetime = datetime.now(UTC)
        session.add(
            AssetRow(
                id=uuid4(),
                kind=AssetKind.FILE.value,
                name="stray",
                slug="stray",
                workspace_id=SHARED_WORKSPACE_ID,
                properties={},
                status=AssetStatus.ACTIVE.value,
                created_at=now,
                updated_at=now,
            ),
        )
        session.commit()
        _put(store, "spaces/space_analytics.py", _SPACE)
        scanner.scan(now)
        assert session.scalar(select(AssetRow).where(AssetRow.name == "stray")) is None
        assert _space(session).slug == "analytics"
    finally:
        session.close()
        database.dispose()


def test_unresolved_table_applies_on_the_next_scan(tmp_path: Path) -> None:
    """A missing table stays unresolved until the catalog row exists, without re-executing."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
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
        assert executor.workloads == []
        _table(session, "iceberg.sales.orders")
        scanner.scan(datetime.now(UTC))
        session.refresh(pending)
        assert pending.status == DefinitionItemStatus.APPLIED.value
        assert pending.asset_id is not None
        assert pending.run_status == DefinitionRunStatus.QUEUED.value
        assert len(executor.workloads) == 1
    finally:
        session.close()
        database.dispose()


def test_scope_before_its_space_resolves_on_retry(tmp_path: Path) -> None:
    """A scope uploaded before its space is applied on the scan that first sees the space."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
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
        assert executor.workloads == []
    finally:
        session.close()
        database.dispose()


def test_bad_name_and_bad_file_do_not_block_a_good_file(tmp_path: Path) -> None:
    """A misnamed Python file is an error row and is not executed. A syntax error stays local."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
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
        assert executor.workloads == []
    finally:
        session.close()
        database.dispose()


def test_airflow_and_page_operators_link_existing_rows(tmp_path: Path) -> None:
    """AirflowOperator and PageOperator attach the synced DAG and the workspace file."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _dag(session)
        _node(session, "/docs/sales.md")
        _put(store, "spaces/space_analytics.py", _SPACE)
        source: str = (
            "from intelegent_data_catalog.assets.dsl import Asset, AirflowOperator, PageOperator\n"
            'Asset("etl", AirflowOperator(instance="prod-airflow", dag_id="etl_orders"))\n'
            'Asset("readme", PageOperator(path="/docs/sales.md"))\n'
        )
        _put(store, "assets/sales/asset_links.py", source)
        scanner.scan(datetime.now(UTC))
        kinds: set[str] = set(session.scalars(select(AssetRow.kind)))
        assert kinds == {AssetKind.AIRFLOW_DAG.value, AssetKind.FILE.value}
        assert session.scalar(select(AssetMembershipRow)) is None
        assert len(executor.workloads) == 2
    finally:
        session.close()
        database.dispose()


def test_airflow_instance_operator_links_existing_instance(tmp_path: Path) -> None:
    """AirflowInstanceOperator attaches the registered instance and exposes its detail."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _dag(session)
        source: str = (
            "from intelegent_data_catalog.assets.dsl import Asset, AirflowInstanceOperator\n"
            'Asset("prod", AirflowInstanceOperator(instance="prod-airflow"))\n'
        )
        _put(store, "assets/sales/asset_instance.py", source)
        scanner.scan(datetime.now(UTC))
        asset: AssetRow | None = session.scalar(
            select(AssetRow).where(AssetRow.kind == AssetKind.AIRFLOW_INSTANCE.value),
        )
        instance: AirflowInstanceRow | None = session.scalar(select(AirflowInstanceRow))
        assert asset is not None
        assert instance is not None
        assert instance.asset_id == asset.id
        assert len(executor.workloads) == 1
        loaded: BaseModel = AssetRegistry(session, _UnavailableAgents()).load(asset.id).detail()
        assert isinstance(loaded, InstanceAssetDetail)
        detail: InstanceAssetDetail = loaded
        assert detail.instance_id == instance.id
        assert detail.slug == "prod-airflow"
        assert detail.status == AirflowInstanceStatus.ACTIVE.value
    finally:
        session.close()
        database.dispose()


def test_missing_airflow_instance_stays_unresolved(tmp_path: Path) -> None:
    """A slug with no registered instance does not create an asset."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        source: str = (
            "from intelegent_data_catalog.assets.dsl import Asset, AirflowInstanceOperator\n"
            'Asset("prod", AirflowInstanceOperator(instance="missing-airflow"))\n'
        )
        _put(store, "assets/sales/asset_missing.py", source)
        scanner.scan(datetime.now(UTC))
        item: AssetDefinitionItemRow | None = session.scalar(select(AssetDefinitionItemRow))
        assert item is not None
        assert item.status == DefinitionItemStatus.UNRESOLVED.value
        assert session.scalar(select(AssetRow)) is None
        assert executor.workloads == []
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
        _RecordingExecutor(),
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
        assert "Space(" in str(body["source"])
        empty: httpx.Response = _send(app, "GET", "/api/v1/asset-definitions/files?folder=assets")
        assert empty.json() == []
        queued: httpx.Response = _send(app, "POST", "/api/v1/asset-definitions/scan")
        assert queued.status_code == 202
        assert queued.json()["queued"] is True
    finally:
        database.dispose()


def test_api_lists_objects_under_the_assets_folder(tmp_path: Path) -> None:
    """GET /objects returns every file under the assets folder, including non-Python files."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    store: MemoryObjectStore = MemoryObjectStore()
    _put(store, "assets/sales/asset_orders.py", "asset = 1\n")
    _put(store, "assets/sales/notes.txt", "hello\n")
    _put(store, "assets/sales/", "")
    _put(store, "spaces/space_analytics.py", _SPACE)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.asset_definition_store.override(providers.Object(store))
    app: FastAPI = create_app(container)
    try:
        path: str = "/api/v1/asset-definitions/objects?folder=assets"
        listed: httpx.Response = _send(app, "GET", path)
        assert listed.status_code == 200
        rows: list[object] = listed.json()
        paths: list[str] = []
        row: object
        for row in rows:
            assert isinstance(row, dict)
            paths.append(str(row["path"]))
        assert paths == ["sales/asset_orders.py", "sales/notes.txt"]
        scanned: object
        for scanned in rows:
            assert isinstance(scanned, dict)
            assert scanned["asset_ids"] == []
            assert scanned["file_id"] is None
    finally:
        database.dispose()


def test_api_lists_catalog_assets_included_by_each_file(tmp_path: Path) -> None:
    """A scanned definition file reports the catalog assets it includes. Other files do not."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AssetDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    asset_id: str = ""
    file_id: str = ""
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "spaces/space_analytics.py", _SPACE)
        _put(store, "scopes/analytics/scope_finance.py", _SCOPE)
        _put(store, "assets/sales/asset_orders.py", _ORDERS)
        _put(store, "assets/sales/notes.txt", "hello\n")
        scanner.scan(datetime.now(UTC))
        asset: AssetRow | None = session.scalar(select(AssetRow).where(AssetRow.name == "orders"))
        assert asset is not None
        assert len(executor.workloads) == 1
        asset_id = str(asset.id)
        definition: AssetDefinitionFileRow | None = session.scalar(
            select(AssetDefinitionFileRow).where(
                AssetDefinitionFileRow.key == f"{_PREFIX}assets/sales/asset_orders.py",
            ),
        )
        assert definition is not None
        file_id = str(definition.id)
    finally:
        session.close()
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.asset_definition_store.override(providers.Object(store))
    app: FastAPI = create_app(container)
    try:
        path: str = "/api/v1/asset-definitions/objects?folder=assets"
        listed: httpx.Response = _send(app, "GET", path)
        assert listed.status_code == 200
        rows: list[object] = listed.json()
        by_path: dict[str, dict[str, object]] = {}
        row: object
        for row in rows:
            assert isinstance(row, dict)
            by_path[str(row["path"])] = row
        orders: dict[str, object] = by_path["sales/asset_orders.py"]
        notes: dict[str, object] = by_path["sales/notes.txt"]
        assert orders["asset_ids"] == [asset_id]
        assert orders["file_id"] == file_id
        assert notes["asset_ids"] == []
        assert notes["file_id"] is None
        listed_assets: httpx.Response = _send(app, "GET", "/api/v1/assets?q=orders")
        assert listed_assets.status_code == 200
        payload: dict[str, object] = listed_assets.json()
        found_items: object = payload["items"]
        assert isinstance(found_items, list)
        assert len(found_items) == 1
        asset_row: object = found_items[0]
        assert isinstance(asset_row, dict)
        assert asset_row["refresh_mode"] == "INTERVAL"
        assert asset_row["interval_seconds"] == 600
        assert asset_row["schedule"] == "0 2 * * *"
        assert asset_row["next_run_at"] is None
    finally:
        database.dispose()


class _Context(OperatorContext):
    """Records one execute call."""

    def __init__(self) -> None:
        super().__init__(uuid4(), "orders", logging.getLogger("test.operator"))
        self.flags: dict[str, bool] = {}
        self.described: bool = False

    def build_context(self, flags: dict[str, bool]) -> None:
        self.flags = dict(flags)

    def describe(self) -> None:
        self.described = True


class _Quiet:
    """Operator stand-in that succeeds without calling the model."""

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        del context
        if observer is None:
            return
        observer.begin("build_context")
        observer.end("build_context", None)
        observer.begin("describe")
        observer.end("describe", None)


class _Boom:
    """Operator stand-in that fails with a catalog error."""

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        del context, observer
        raise CatalogError("nope")


def test_operator_round_trip_and_default_execute() -> None:
    """A stored operator rebuilds, and execute builds context then describes it."""
    iceberg: IcebergTableOperator = IcebergTableOperator(
        source_key="iceberg.sales.orders",
        include_lineage=False,
    )
    restored: IcebergTableOperator = operator_from_ref(iceberg.to_ref())
    assert isinstance(restored, IcebergTableOperator)
    assert restored == iceberg
    page: PageOperator = PageOperator(path="/docs/sales.md")
    assert operator_from_ref(page.to_ref()) == page
    postgres: PostgresTableOperator = PostgresTableOperator(source_key="app.public.customers")
    assert operator_from_ref(postgres.to_ref()) == postgres
    dag: AirflowOperator = AirflowOperator(instance="prod-airflow", dag_id="etl")
    assert operator_from_ref(dag.to_ref()) == dag
    instance_operator: AirflowInstanceOperator = AirflowInstanceOperator(instance="prod-airflow")
    assert operator_from_ref(instance_operator.to_ref()) == instance_operator
    with pytest.raises(AssetDefinitionError, match="unknown operator"):
        operator_from_ref(OperatorRef(type="MissingOperator", params={}))
    context: _Context = _Context()
    iceberg.execute(context)
    assert context.flags["include_lineage"] is False
    assert context.flags["include_graph"] is True
    assert "use_rules" not in context.flags
    assert "use_skills" not in context.flags
    assert "use_rules" not in iceberg.to_ref().params
    assert context.described is True
    explicit: IcebergTableOperator = IcebergTableOperator(
        source_key="iceberg.sales.orders",
        use_rules=False,
    )
    assert explicit.context_flags()["use_rules"] is False
    assert "use_skills" not in explicit.context_flags()
    assert explicit.to_ref().params["use_rules"] is False
    assert "use_skills" not in explicit.to_ref().params


def test_local_and_celery_executors_submit_the_workload() -> None:
    """Local runs inline. Celery hands the JSON payload to the sender."""
    workload: OperatorWorkload = OperatorWorkload(
        item_id=uuid4(),
        asset_id=uuid4(),
        asset_name="orders",
        file_key="asset-definitions/assets/asset_orders.py",
        item_key="orders",
        operator=IcebergTableOperator(source_key="a.b.c").to_ref(),
    )
    seen: list[OperatorWorkload] = []
    LocalOperatorExecutor(seen.append).submit(workload)
    assert seen == [workload]
    sent: list[str] = []
    CeleryOperatorExecutor(send=sent.append).submit(workload)
    assert OperatorWorkload.model_validate_json(sent[0]) == workload


def test_runner_records_success_and_advances_cron(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished run stores succeeded and the next cron tick."""
    database, session, _store, scanner, executor = _open(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    try:
        _table(session, "iceberg.sales.orders")
        _put(_store, "assets/sales/asset_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        workload: OperatorWorkload = executor.workloads[0]
        monkeypatch.setattr(
            "intelegent_data_catalog.assets.execution.runtime.operator_from_ref",
            lambda _ref: _Quiet(),
        )
        OperatorRunner(container, logging.getLogger("test.runner")).run(workload)
        item: AssetDefinitionItemRow | None = session.get(AssetDefinitionItemRow, workload.item_id)
        assert item is not None
        session.refresh(item)
        assert item.run_status == DefinitionRunStatus.SUCCEEDED.value
        assert item.ran_at is not None
        assert item.schedule == "0 2 * * *"
        assert item.ran_at is not None
        assert item.next_run_at == next_cron(item.schedule or "", item.ran_at)
    finally:
        session.close()
        database.dispose()


def test_runner_records_failure_and_still_advances_cron(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catalog error is stored, and a cron schedule still moves forward."""
    database, session, store, scanner, executor = _open(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "assets/sales/asset_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        workload: OperatorWorkload = executor.workloads[0]
        monkeypatch.setattr(
            "intelegent_data_catalog.assets.execution.runtime.operator_from_ref",
            lambda _ref: _Boom(),
        )
        OperatorRunner(container, logging.getLogger("test.runner")).run(workload)
        item: AssetDefinitionItemRow | None = session.get(AssetDefinitionItemRow, workload.item_id)
        assert item is not None
        session.refresh(item)
        assert item.run_status == DefinitionRunStatus.FAILED.value
        assert item.run_error == "nope"
        assert item.ran_at is not None
        assert item.ran_at is not None
        assert item.next_run_at == next_cron("0 2 * * *", item.ran_at)
    finally:
        session.close()
        database.dispose()


def test_runner_without_schedule_does_not_repeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing schedule runs once and leaves next_run_at empty."""
    database, session, store, scanner, executor = _open(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    source: str = (
        "from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator\n"
        'Asset("once", IcebergTableOperator(source_key="iceberg.sales.orders"))\n'
    )
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "assets/sales/asset_once.py", source)
        scanner.scan(datetime.now(UTC))
        monkeypatch.setattr(
            "intelegent_data_catalog.assets.execution.runtime.operator_from_ref",
            lambda _ref: _Quiet(),
        )
        OperatorRunner(container, logging.getLogger("test.runner")).run(executor.workloads[0])
        item: AssetDefinitionItemRow | None = session.scalar(
            select(AssetDefinitionItemRow).where(AssetDefinitionItemRow.item_key == "once"),
        )
        assert item is not None
        session.refresh(item)
        assert item.run_status == DefinitionRunStatus.SUCCEEDED.value
        assert item.schedule is None
        assert item.next_run_at is None
    finally:
        session.close()
        database.dispose()


def test_claim_due_submits_past_and_skips_future(tmp_path: Path) -> None:
    """Only a finished item whose next_run_at is in the past is queued again."""
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "assets/sales/asset_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        item: AssetDefinitionItemRow | None = session.scalar(
            select(AssetDefinitionItemRow).where(AssetDefinitionItemRow.item_key == "orders"),
        )
        assert item is not None
        item.run_status = DefinitionRunStatus.SUCCEEDED.value
        item.next_run_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
        executor.workloads.clear()
        claimed: int = AssetOperatorSchedule(session, executor).claim_due(datetime.now(UTC))
        assert claimed == 1
        assert executor.workloads[0].item_key == "orders"
        session.refresh(item)
        assert item.run_status == DefinitionRunStatus.QUEUED.value
        item.run_status = DefinitionRunStatus.SUCCEEDED.value
        item.next_run_at = datetime.now(UTC) + timedelta(days=2)
        session.commit()
        executor.workloads.clear()
        assert AssetOperatorSchedule(session, executor).claim_due(datetime.now(UTC)) == 0
        assert executor.workloads == []
        asset: AssetRow | None = session.get(AssetRow, item.asset_id)
        assert asset is not None
        asset.status = AssetStatus.DEPRECATED.value
        item.run_status = DefinitionRunStatus.SUCCEEDED.value
        item.next_run_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
        executor.workloads.clear()
        assert AssetOperatorSchedule(session, executor).claim_due(datetime.now(UTC)) == 0
        assert executor.workloads == []
    finally:
        session.close()
        database.dispose()


def test_migration_adds_run_columns(tmp_path: Path) -> None:
    """Upgrade adds the operator run columns on asset_definition_items."""
    database, session, _store, _scanner, _executor = _open(tmp_path)
    try:
        names: set[str] = {column.name for column in AssetDefinitionItemRow.__table__.columns}
        assert {
            "run_status",
            "run_error",
            "ran_at",
            "schedule",
            "next_run_at",
        } <= names
        session.execute(select(AssetDefinitionItemRow.run_status))
    finally:
        session.close()
        database.dispose()


def _table_operator() -> IcebergTableOperator:
    operator: IcebergTableOperator = IcebergTableOperator(source_key="iceberg.sales.orders")
    return operator


def _open(
    tmp_path: Path,
) -> tuple[CatalogDatabase, Session, MemoryObjectStore, AssetDefinitionScanner, _RecordingExecutor]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    session: Session = database.session()
    store: MemoryObjectStore = MemoryObjectStore()
    settings: AssetDefinitionsSettings = AssetDefinitionsSettings(enabled=True, prefix=_PREFIX)
    executor: _RecordingExecutor = _RecordingExecutor()
    scanner: AssetDefinitionScanner = AssetDefinitionScanner(
        session,
        store,
        settings,
        logging.getLogger("test.asset_definitions"),
        executor,
    )
    return database, session, store, scanner, executor


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
