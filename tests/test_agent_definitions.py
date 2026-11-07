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

from intelegent_data_catalog.agents.base import ContextBuilder, ContextBuilderProvider
from intelegent_data_catalog.agents.definitions.models import (
    AgentSpec,
    DefinitionSpec,
    OperatorRef,
    PageSourceRef,
    TableSourceRef,
)
from intelegent_data_catalog.agents.definitions.scanner import AgentDefinitionScanner
from intelegent_data_catalog.agents.dsl import (
    Agent,
    BuildContext,
    Describe,
    IcebergTable,
    PostgresTable,
    Schema,
)
from intelegent_data_catalog.agents.dsl.collect import collect_definitions
from intelegent_data_catalog.agents.dsl.errors import (
    AgentDefinitionError,
    AgentDefinitionImportError,
)
from intelegent_data_catalog.agents.dsl.operators.base import OperatorContext, OperatorObserver
from intelegent_data_catalog.agents.dsl.operators.registry import operator_from_ref
from intelegent_data_catalog.agents.execution.executor import (
    CeleryOperatorExecutor,
    LocalOperatorExecutor,
)
from intelegent_data_catalog.agents.execution.runtime import OperatorRunner
from intelegent_data_catalog.agents.execution.tasks import BUILD_CONTEXT, DESCRIBE
from intelegent_data_catalog.agents.execution.workload import OperatorWorkload
from intelegent_data_catalog.agents.registry import AgentRegistry
from intelegent_data_catalog.agents.service import _UnavailableAgents
from intelegent_data_catalog.agents.strategies.instance import InstanceAgentDetail
from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import AgentDefinitionsSettings
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.runtime import set_job_container
from intelegent_data_catalog.jobs.tasks import run_locked_scan
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    AgentKind,
    AgentStatus,
    AirflowInstanceStatus,
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
    AgentDefinitionFileRow,
    AgentDefinitionItemRow,
    AgentRow,
    AirflowDagRow,
    AirflowInstanceRow,
    CatalogTableRow,
    DataSourceRow,
    UserRow,
    WorkspaceNodeRow,
)
from intelegent_data_catalog.schedual.cron import next_cron
from intelegent_data_catalog.schedual.operators import AgentOperatorSchedule
from intelegent_data_catalog.storage.object_store import MemoryObjectStore

_PREFIX: str = "agent-definitions/"
_ORDERS: str = (
    "from intelegent_data_catalog.agents.dsl import Agent, BuildContext, Describe, IcebergTable\n"
    "Agent(\n"
    '    "orders",\n'
    '    IcebergTable(source_key="iceberg.sales.orders", data_source="lakehouse"),\n'
    "    operators=[BuildContext(include_lineage=True), Describe()],\n"
    '    schedule="0 2 * * *",\n'
    ")\n"
)
_TWO_TABLES: str = (
    _ORDERS + "Agent(\n"
    '    "returns",\n'
    '    IcebergTable(source_key="iceberg.sales.returns", data_source="lakehouse"),\n'
    "    operators=[BuildContext(), Describe()],\n"
    ")\n"
)


class _RecordingExecutor:
    """Remembers submitted workloads and does not run them."""

    def __init__(self) -> None:
        self.workloads: list[OperatorWorkload] = []

    def submit(self, workload: OperatorWorkload) -> None:
        self.workloads.append(workload)


def test_collect_each_kind() -> None:
    """An agents file yields the declared agent."""
    agents: DefinitionSpec = collect_definitions(
        _ORDERS,
        "agent_orders.py",
        DefinitionKind.AGENT,
        timeout_seconds=5,
    )
    assert agents.agents[0].key == "orders"
    assert agents.agents[0].schedule == "0 2 * * *"
    assert [item.type for item in agents.agents[0].operators] == ["BuildContext", "Describe"]
    assert agents.agents[0].source.kind == "table"
    assert agents.agents[0].tags == []
    assert "tags" not in agents.agents[0].set_fields


def test_agent_accepts_keyword_fields_and_tags() -> None:
    """Tags are optional labels. Blanks and repeats are dropped. Order stays."""
    source: str = (
        "from intelegent_data_catalog.agents.dsl import "
        "Agent, BuildContext, Describe, IcebergTable\n"
        "agent = Agent(\n"
        '    key="customer_spend",\n'
        '    description="Spend and order count per customer, built from customers and orders.",\n'
        '    context="Grain is one row per customer.",\n'
        "    source=IcebergTable(\n"
        '        source_key="polaris.sales.customer_spend",\n'
        '        data_source="Polaris",\n'
        "    ),\n"
        "    operators=[BuildContext(include_lineage=True), Describe()],\n"
        '    properties={"owner": "commerce-analytics", "tier": "gold"},\n'
        '    tags=[" gold ", "customer", "gold"],\n'
        '    schedule="0 2 * * *",\n'
        ")\n"
    )
    spec: DefinitionSpec = collect_definitions(
        source,
        "agent_customer_spend.py",
        DefinitionKind.AGENT,
        timeout_seconds=5,
    )
    assert spec.agents[0].key == "customer_spend"
    assert spec.agents[0].name is None
    assert "name" not in spec.agents[0].set_fields
    assert spec.agents[0].tags == ["gold", "customer"]
    assert "tags" in spec.agents[0].set_fields
    assert spec.agents[0].properties == {"owner": "commerce-analytics", "tier": "gold"}


def test_bad_tags_fail_the_file() -> None:
    """A tag that is not a short label fails the definition file."""
    source: str = (
        "from intelegent_data_catalog.agents.dsl import Agent, IcebergTable\n"
        'Agent("orders", IcebergTable(source_key="a.b.c"), tags=["ok", "bad/tag"])\n'
    )
    with pytest.raises(AgentDefinitionImportError, match="each tag"):
        collect_definitions(source, "agent_tags.py", DefinitionKind.AGENT, timeout_seconds=5)


def test_scan_stores_declared_tags(tmp_path: Path) -> None:
    """A tags argument is stored on the agent and returned with the row."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, _executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        source: str = (
            "from intelegent_data_catalog.agents.dsl import Agent, IcebergTable\n"
            "Agent(\n"
            '    key="order_mart",\n'
            '    source=IcebergTable(source_key="iceberg.sales.orders", '
            'data_source="lakehouse"),\n'
            '    tags=["gold", "orders"],\n'
            ")\n"
        )
        _put(store, "agents/agent_orders.py", source)
        scanner.scan(datetime.now(UTC))
        agent: AgentRow | None = session.scalar(
            select(AgentRow).where(AgentRow.name == "order_mart"),
        )
        assert agent is not None
        assert agent.properties["tags"] == ["gold", "orders"]
    finally:
        session.close()
        database.dispose()


def test_iceberg_and_postgres_operators_share_table_fields() -> None:
    """Concrete table operators build a table source. The base stays abstract."""
    iceberg: IcebergTable = IcebergTable(
        source_key="iceberg.sales.orders",
        data_source="lakehouse",
    )
    postgres: PostgresTable = PostgresTable(
        source_key="app.public.customers",
        data_source="app",
    )
    iceberg_source: TableSourceRef = iceberg.to_ref()
    postgres_source: TableSourceRef = postgres.to_ref()
    assert iceberg_source.source_key == "iceberg.sales.orders"
    assert iceberg_source.data_source == "lakehouse"
    assert postgres_source.source_key == "app.public.customers"
    assert postgres_source.data_source == "app"
    assert IcebergTable(source_key="iceberg.sales.orders").data_source is None
    with pytest.raises(ValidationError, match="data_source cannot be blank"):
        IcebergTable(source_key="iceberg.sales.orders", data_source=" ")


def test_page_operator_defaults_workspace_to_shared() -> None:
    """Page without a workspace names the shared workspace."""
    source: str = (
        "from intelegent_data_catalog.agents.dsl import Agent, Page\n"
        'Agent("readme", Page(path="/docs/sales.md"))\n'
    )
    spec: DefinitionSpec = collect_definitions(
        source,
        "agent_readme.py",
        DefinitionKind.AGENT,
        timeout_seconds=5,
    )
    assert isinstance(spec.agents[0].source, PageSourceRef)
    assert spec.agents[0].source.workspace == "shared"


def test_wrong_kind_duplicate_target_and_syntax_fail_the_file() -> None:
    """Duplicate keys and syntax errors are import errors."""
    duplicated: str = _ORDERS + (
        "Agent(\n"
        '    "orders",\n'
        '    IcebergTable(source_key="iceberg.sales.orders"),\n'
        ")\n"
    )
    with pytest.raises(AgentDefinitionImportError, match="duplicate key"):
        collect_definitions(duplicated, "agent_dup.py", DefinitionKind.AGENT, timeout_seconds=5)
    bad_cron: str = (
        "from intelegent_data_catalog.agents.dsl import Agent, IcebergTable\n"
        'Agent("orders", IcebergTable(source_key="a.b.c"), schedule="daily")\n'
    )
    with pytest.raises(AgentDefinitionImportError, match="five-field"):
        collect_definitions(bad_cron, "agent_cron.py", DefinitionKind.AGENT, timeout_seconds=5)
    with pytest.raises(TypeError):
        Agent("orders", _table_operator(), scope="analytics/finance")  # type: ignore[call-arg]
    with pytest.raises(AgentDefinitionImportError, match="syntax error"):
        collect_definitions("def (\n", "agent_syntax.py", DefinitionKind.AGENT, timeout_seconds=5)


def test_definition_outside_a_file_raises() -> None:
    """Agent registers only while a file is being collected."""
    with pytest.raises(AgentDefinitionError, match="collected"):
        Agent("orders", _table_operator())


def test_collect_times_out() -> None:
    """A file that does not finish becomes an import error."""
    source: str = "import time\ntime.sleep(2)\n"
    with pytest.raises(AgentDefinitionImportError, match="timed out"):
        collect_definitions(source, "agent_slow.py", DefinitionKind.AGENT, timeout_seconds=0.05)


def test_scan_creates_an_agent_in_one_tick(tmp_path: Path) -> None:
    """Nested agent keys are found. Unknown folders and non-Python files are ignored."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "agents/a/b/agent_orders.py", _ORDERS)
        _put(store, "agents/readme.txt", "ignore")
        _put(store, "notes/agent_other.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        agent: AgentRow | None = session.scalar(select(AgentRow).where(AgentRow.name == "orders"))
        assert agent is not None
        assert agent.kind == AgentKind.TABLE.value
        assert agent.name == "orders"
        assert agent.context is None
        item: AgentDefinitionItemRow | None = session.scalar(
            select(AgentDefinitionItemRow).where(AgentDefinitionItemRow.item_key == "orders"),
        )
        assert item is not None
        assert item.run_status == DefinitionRunStatus.QUEUED.value
        assert item.schedule == "0 2 * * *"
        assert len(executor.workloads) == 1
        keys: set[str] = set(session.scalars(select(AgentDefinitionFileRow.key)))
        assert f"{_PREFIX}notes/agent_other.py" not in keys
        assert f"{_PREFIX}agents/readme.txt" not in keys
    finally:
        session.close()
        database.dispose()


def test_same_etag_is_a_no_op_and_a_change_updates_and_detaches(tmp_path: Path) -> None:
    """Unchanged bytes leave catalog edits in place. A smaller file drops the extra agent."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _table(session, "iceberg.sales.returns")
        _put(store, "agents/sales/agent_orders.py", _TWO_TABLES)
        scanner.scan(datetime.now(UTC))
        assert len(executor.workloads) == 2
        executor.workloads.clear()
        orders: AgentRow | None = session.scalar(
            select(AgentRow).where(AgentRow.name == "orders"),
        )
        assert orders is not None
        orders.description = "hacked"
        session.commit()
        scanner.scan(datetime.now(UTC))
        session.refresh(orders)
        assert executor.workloads == []
        assert orders.description == "hacked"
        _put(store, "agents/sales/agent_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        session.refresh(orders)
        assert orders.description == "hacked"
        names: set[str] = set(session.scalars(select(AgentRow.name)))
        assert names == {"orders"}
        assert executor.workloads == []
    finally:
        session.close()
        database.dispose()


def test_deleted_script_removes_the_agent(tmp_path: Path) -> None:
    """A key missing from the listing deletes its agent."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        agent_key: str = f"{_PREFIX}agents/sales/agent_orders.py"
        _put(store, "agents/sales/agent_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        assert len(executor.workloads) == 1
        store.delete(agent_key)
        scanner.scan(datetime.now(UTC))
        removed: AgentDefinitionFileRow | None = session.scalar(
            select(AgentDefinitionFileRow).where(AgentDefinitionFileRow.key == agent_key),
        )
        assert removed is not None
        assert removed.status == DefinitionFileStatus.REMOVED.value
        assert session.scalar(select(AgentRow).where(AgentRow.name == "orders")) is None
    finally:
        session.close()
        database.dispose()


def test_scan_drops_agents_no_script_declares(tmp_path: Path) -> None:
    """An agent that no agent script names is deleted on the next scan."""
    database: CatalogDatabase
    session: Session
    scanner: AgentDefinitionScanner
    database, session, _store, scanner, _executor = _open(tmp_path)
    try:
        now: datetime = datetime.now(UTC)
        session.add(
            AgentRow(
                id=uuid4(),
                kind=AgentKind.FILE.value,
                name="stray",
                slug="stray",
                workspace_id=SHARED_WORKSPACE_ID,
                properties={},
                status=AgentStatus.ACTIVE.value,
                created_at=now,
                updated_at=now,
            ),
        )
        session.commit()
        scanner.scan(now)
        assert session.scalar(select(AgentRow).where(AgentRow.name == "stray")) is None
    finally:
        session.close()
        database.dispose()


def test_unresolved_table_applies_on_the_next_scan(tmp_path: Path) -> None:
    """A missing table stays unresolved until the catalog row exists, without re-executing."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _put(store, "agents/sales/agent_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        pending: AgentDefinitionItemRow | None = session.scalar(
            select(AgentDefinitionItemRow).where(AgentDefinitionItemRow.item_key == "orders"),
        )
        assert pending is not None
        assert pending.status == DefinitionItemStatus.UNRESOLVED.value
        assert executor.workloads == []
        _table(session, "iceberg.sales.orders")
        scanner.scan(datetime.now(UTC))
        session.refresh(pending)
        assert pending.status == DefinitionItemStatus.APPLIED.value
        assert pending.agent_id is not None
        assert pending.run_status == DefinitionRunStatus.QUEUED.value
        assert len(executor.workloads) == 1
    finally:
        session.close()
        database.dispose()


def test_bad_name_and_bad_file_do_not_block_a_good_file(tmp_path: Path) -> None:
    """A misnamed Python file is an error row and is not executed. A syntax error stays local."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _put(store, "agents/orders.py", _ORDERS)
        _put(store, "agents/agent_bad.py", "def (\n")
        _put(store, "agents/agent_orders.py", _ORDERS)
        _table(session, "iceberg.sales.orders")
        scanner.scan(datetime.now(UTC))
        applied: AgentRow | None = session.scalar(select(AgentRow).where(AgentRow.name == "orders"))
        assert applied is not None
        misnamed: AgentDefinitionFileRow | None = session.scalar(
            select(AgentDefinitionFileRow).where(
                AgentDefinitionFileRow.key == f"{_PREFIX}agents/orders.py",
            ),
        )
        assert misnamed is not None
        assert misnamed.status == DefinitionFileStatus.ERROR.value
        assert misnamed.error is not None
        assert "agent_" in misnamed.error
        assert (
            session.scalar(
                select(AgentDefinitionItemRow).where(
                    AgentDefinitionItemRow.file_id == misnamed.id,
                ),
            )
            is None
        )
        broken: AgentDefinitionFileRow | None = session.scalar(
            select(AgentDefinitionFileRow).where(
                AgentDefinitionFileRow.key == f"{_PREFIX}agents/agent_bad.py",
            ),
        )
        assert broken is not None
        assert broken.status == DefinitionFileStatus.ERROR.value
        assert len(executor.workloads) == 1
    finally:
        session.close()
        database.dispose()


def test_airflow_and_page_operators_link_existing_rows(tmp_path: Path) -> None:
    """AirflowDag and Page attach the synced DAG and the workspace file."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _dag(session)
        _node(session, "/docs/sales.md")
        source: str = (
            "from intelegent_data_catalog.agents.dsl import "
            "Agent, AirflowDag, BuildContext, Describe, Page\n"
            'Agent("etl", AirflowDag(instance="prod-airflow", dag_id="etl_orders"), '
            "operators=[BuildContext(), Describe()])\n"
            'Agent("readme", Page(path="/docs/sales.md"), '
            "operators=[BuildContext(), Describe()])\n"
        )
        _put(store, "agents/sales/agent_links.py", source)
        scanner.scan(datetime.now(UTC))
        kinds: set[str] = set(session.scalars(select(AgentRow.kind)))
        assert kinds == {AgentKind.AIRFLOW_DAG.value, AgentKind.FILE.value}
        assert len(executor.workloads) == 2
    finally:
        session.close()
        database.dispose()


def test_airflow_instance_operator_links_existing_instance(tmp_path: Path) -> None:
    """AirflowInstance attaches the registered instance and exposes its detail."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _dag(session)
        source: str = (
            "from intelegent_data_catalog.agents.dsl import "
            "Agent, AirflowInstance, BuildContext, Describe\n"
            'Agent("prod", AirflowInstance(instance="prod-airflow"), '
            "operators=[BuildContext(), Describe()])\n"
        )
        _put(store, "agents/sales/agent_instance.py", source)
        scanner.scan(datetime.now(UTC))
        agent: AgentRow | None = session.scalar(
            select(AgentRow).where(AgentRow.kind == AgentKind.AIRFLOW_INSTANCE.value),
        )
        instance: AirflowInstanceRow | None = session.scalar(select(AirflowInstanceRow))
        assert agent is not None
        assert instance is not None
        assert instance.agent_id == agent.id
        assert len(executor.workloads) == 1
        loaded: BaseModel = AgentRegistry(session, _UnavailableAgents()).load(agent.id).detail()
        assert isinstance(loaded, InstanceAgentDetail)
        detail: InstanceAgentDetail = loaded
        assert detail.instance_id == instance.id
        assert detail.slug == "prod-airflow"
        assert detail.status == AirflowInstanceStatus.ACTIVE.value
    finally:
        session.close()
        database.dispose()


def test_missing_airflow_instance_stays_unresolved(tmp_path: Path) -> None:
    """A slug with no registered instance does not create an agent."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        source: str = (
            "from intelegent_data_catalog.agents.dsl import Agent, AirflowInstance\n"
            'Agent("prod", AirflowInstance(instance="missing-airflow"))\n'
        )
        _put(store, "agents/sales/agent_missing.py", source)
        scanner.scan(datetime.now(UTC))
        item: AgentDefinitionItemRow | None = session.scalar(select(AgentDefinitionItemRow))
        assert item is not None
        assert item.status == DefinitionItemStatus.UNRESOLVED.value
        assert session.scalar(select(AgentRow)) is None
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


def test_locked_scan_applies_agent_and_releases(tmp_path: Path) -> None:
    """The locked runner scans the configured store and releases the lock."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    store: MemoryObjectStore = MemoryObjectStore()
    _put(store, "agents/agent_orders.py", _ORDERS)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.agent_definition_store.override(providers.Object(store))
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
            found: AgentDefinitionFileRow | None = session.scalar(select(AgentDefinitionFileRow))
            assert found is not None
            assert found.key.endswith("agents/agent_orders.py")
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
    settings: AgentDefinitionsSettings = AgentDefinitionsSettings(enabled=True, prefix=_PREFIX)
    scanner: AgentDefinitionScanner = AgentDefinitionScanner(
        session,
        store,
        settings,
        logging.getLogger("test.agent_definitions"),
        _RecordingExecutor(),
    )
    try:
        _put(store, "agents/agent_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
    finally:
        session.close()
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.agent_definition_store.override(providers.Object(store))
    app: FastAPI = create_app(container)
    try:
        listed: httpx.Response = _send(app, "GET", "/api/v1/agent-definitions/files?folder=agents")
        assert listed.status_code == 200
        rows: list[object] = listed.json()
        assert len(rows) == 1
        assert isinstance(rows[0], dict)
        file_id: str = str(rows[0]["id"])
        assert rows[0]["status"] == "OK"
        detail: httpx.Response = _send(app, "GET", f"/api/v1/agent-definitions/files/{file_id}")
        assert detail.status_code == 200
        body: dict[str, object] = detail.json()
        items: object = body["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        assert "Agent(" in str(body["source"])
        queued: httpx.Response = _send(app, "POST", "/api/v1/agent-definitions/scan")
        assert queued.status_code == 202
        assert queued.json()["queued"] is True
    finally:
        database.dispose()


def test_api_lists_objects_under_the_agents_folder(tmp_path: Path) -> None:
    """GET /objects returns every file under the agents folder, including non-Python files."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    store: MemoryObjectStore = MemoryObjectStore()
    _put(store, "agents/sales/agent_orders.py", "agent = 1\n")
    _put(store, "agents/sales/notes.txt", "hello\n")
    _put(store, "agents/sales/", "")
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.agent_definition_store.override(providers.Object(store))
    app: FastAPI = create_app(container)
    try:
        path: str = "/api/v1/agent-definitions/objects?folder=agents"
        listed: httpx.Response = _send(app, "GET", path)
        assert listed.status_code == 200
        rows: list[object] = listed.json()
        paths: list[str] = []
        row: object
        for row in rows:
            assert isinstance(row, dict)
            paths.append(str(row["path"]))
        assert paths == ["sales/agent_orders.py", "sales/notes.txt"]
        scanned: object
        for scanned in rows:
            assert isinstance(scanned, dict)
            assert scanned["agent_ids"] == []
            assert scanned["file_id"] is None
    finally:
        database.dispose()


def test_api_lists_catalog_agents_included_by_each_file(tmp_path: Path) -> None:
    """A scanned definition file reports the catalog agents it includes. Other files do not."""
    database: CatalogDatabase
    session: Session
    store: MemoryObjectStore
    scanner: AgentDefinitionScanner
    database, session, store, scanner, executor = _open(tmp_path)
    agent_id: str = ""
    file_id: str = ""
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "agents/sales/agent_orders.py", _ORDERS)
        _put(store, "agents/sales/notes.txt", "hello\n")
        scanner.scan(datetime.now(UTC))
        agent: AgentRow | None = session.scalar(select(AgentRow).where(AgentRow.name == "orders"))
        assert agent is not None
        assert len(executor.workloads) == 1
        agent_id = str(agent.id)
        definition: AgentDefinitionFileRow | None = session.scalar(
            select(AgentDefinitionFileRow).where(
                AgentDefinitionFileRow.key == f"{_PREFIX}agents/sales/agent_orders.py",
            ),
        )
        assert definition is not None
        file_id = str(definition.id)
    finally:
        session.close()
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.agent_definition_store.override(providers.Object(store))
    app: FastAPI = create_app(container)
    try:
        path: str = "/api/v1/agent-definitions/objects?folder=agents"
        listed: httpx.Response = _send(app, "GET", path)
        assert listed.status_code == 200
        rows: list[object] = listed.json()
        by_path: dict[str, dict[str, object]] = {}
        row: object
        for row in rows:
            assert isinstance(row, dict)
            by_path[str(row["path"])] = row
        orders: dict[str, object] = by_path["sales/agent_orders.py"]
        notes: dict[str, object] = by_path["sales/notes.txt"]
        assert orders["agent_ids"] == [agent_id]
        assert orders["file_id"] == file_id
        assert notes["agent_ids"] == []
        assert notes["file_id"] is None
        listed_agents: httpx.Response = _send(app, "GET", "/api/v1/agents?q=orders")
        assert listed_agents.status_code == 200
        payload: dict[str, object] = listed_agents.json()
        found_items: object = payload["items"]
        assert isinstance(found_items, list)
        assert len(found_items) == 1
        agent_row: object = found_items[0]
        assert isinstance(agent_row, dict)
        assert agent_row["refresh_mode"] == "INTERVAL"
        assert agent_row["interval_seconds"] == 600
        assert agent_row["schedule"] == "0 2 * * *"
        assert agent_row["next_run_at"] is None
    finally:
        database.dispose()


class _Context(OperatorContext):
    """Records one execute call."""

    def __init__(self) -> None:
        super().__init__(uuid4(), "orders", logging.getLogger("test.operator"))
        self.flags: dict[str, bool] = {}
        self.described: bool = False
        self.context_present: bool = False

    def build_context(self, flags: dict[str, bool]) -> None:
        self.flags = dict(flags)

    def describe(self) -> None:
        self.described = True

    def has_context(self) -> bool:
        return self.context_present


class _Quiet:
    """Operator stand-in that succeeds without calling the model."""

    task_id: str = "build_context"

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

    task_id: str = "build_context"

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        del context, observer
        raise CatalogError("nope")


def test_stored_operator_folds_into_the_operator_list() -> None:
    """An older item row with one operator still loads, and parent_key is dropped."""
    spec: AgentSpec = AgentSpec.model_validate(
        {
            "key": "orders",
            "source": {"kind": "table", "source_key": "a.b.c"},
            "operator": {"type": "BuildContext", "params": {"include_lineage": True}},
            "parent_key": "sales",
        },
    )
    assert [item.type for item in spec.operators] == ["BuildContext"]
    assert spec.operators[0].params["include_lineage"] is True
    assert "parent_key" not in spec.model_dump()
    assert "operator" not in spec.model_dump()


def test_operator_round_trip_and_default_execute() -> None:
    """A stored step rebuilds. BuildContext applies only the flags the file set."""
    source: IcebergTable = IcebergTable(source_key="iceberg.sales.orders")
    assert source.to_ref().kind == "table"
    step: BuildContext = BuildContext(include_lineage=False)
    restored: BuildContext = operator_from_ref(step.to_ref())
    assert isinstance(restored, BuildContext)
    assert restored == step
    with pytest.raises(AgentDefinitionError, match="unknown operator"):
        operator_from_ref(OperatorRef(type="MissingOperator", params={}))
    context: _Context = _Context()
    step.execute(context)
    Describe().execute(context)
    assert context.flags["include_lineage"] is False
    assert "include_graph" not in context.flags
    assert "use_rules" not in context.flags
    assert "use_rules" not in step.to_ref().params
    assert context.described is True
    explicit: BuildContext = BuildContext(use_rules=False)
    assert explicit.context_flags()["use_rules"] is False
    assert "use_skills" not in explicit.context_flags()
    assert explicit.to_ref().params["use_rules"] is False
    assert "use_skills" not in explicit.to_ref().params
    with pytest.raises(AgentDefinitionError, match="does not allow"):
        Agent("orders", source, operators=[BuildContext(include_source=True)])
    with pytest.raises(AgentDefinitionError, match="listed twice"):
        Agent("orders", source, operators=[Describe(), Describe()])


def test_local_and_celery_executors_submit_the_workload() -> None:
    """Local runs inline. Celery hands the JSON payload to the sender."""
    workload: OperatorWorkload = OperatorWorkload(
        item_id=uuid4(),
        agent_id=uuid4(),
        agent_name="orders",
        file_key="agent-definitions/agents/agent_orders.py",
        item_key="orders",
        operators=[BuildContext().to_ref()],
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
        _put(_store, "agents/sales/agent_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        workload: OperatorWorkload = executor.workloads[0]
        monkeypatch.setattr(
            "intelegent_data_catalog.agents.execution.runtime.operator_from_ref",
            lambda _ref: _Quiet(),
        )
        OperatorRunner(container, logging.getLogger("test.runner")).run(workload)
        item: AgentDefinitionItemRow | None = session.get(AgentDefinitionItemRow, workload.item_id)
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
        _put(store, "agents/sales/agent_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        workload: OperatorWorkload = executor.workloads[0]
        monkeypatch.setattr(
            "intelegent_data_catalog.agents.execution.runtime.operator_from_ref",
            lambda _ref: _Boom(),
        )
        OperatorRunner(container, logging.getLogger("test.runner")).run(workload)
        item: AgentDefinitionItemRow | None = session.get(AgentDefinitionItemRow, workload.item_id)
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
        "from intelegent_data_catalog.agents.dsl import "
        "Agent, BuildContext, Describe, IcebergTable\n"
        'Agent("once", IcebergTable(source_key="iceberg.sales.orders"), '
        "operators=[BuildContext(), Describe()])\n"
    )
    try:
        _table(session, "iceberg.sales.orders")
        _put(store, "agents/sales/agent_once.py", source)
        scanner.scan(datetime.now(UTC))
        monkeypatch.setattr(
            "intelegent_data_catalog.agents.execution.runtime.operator_from_ref",
            lambda _ref: _Quiet(),
        )
        OperatorRunner(container, logging.getLogger("test.runner")).run(executor.workloads[0])
        item: AgentDefinitionItemRow | None = session.scalar(
            select(AgentDefinitionItemRow).where(AgentDefinitionItemRow.item_key == "once"),
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
        _put(store, "agents/sales/agent_orders.py", _ORDERS)
        scanner.scan(datetime.now(UTC))
        item: AgentDefinitionItemRow | None = session.scalar(
            select(AgentDefinitionItemRow).where(AgentDefinitionItemRow.item_key == "orders"),
        )
        assert item is not None
        item.run_status = DefinitionRunStatus.SUCCEEDED.value
        item.next_run_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
        executor.workloads.clear()
        claimed: int = AgentOperatorSchedule(session, executor).claim_due(datetime.now(UTC))
        assert claimed == 1
        assert executor.workloads[0].item_key == "orders"
        session.refresh(item)
        assert item.run_status == DefinitionRunStatus.QUEUED.value
        item.run_status = DefinitionRunStatus.SUCCEEDED.value
        item.next_run_at = datetime.now(UTC) + timedelta(days=2)
        session.commit()
        executor.workloads.clear()
        assert AgentOperatorSchedule(session, executor).claim_due(datetime.now(UTC)) == 0
        assert executor.workloads == []
        agent: AgentRow | None = session.get(AgentRow, item.agent_id)
        assert agent is not None
        agent.status = AgentStatus.DEPRECATED.value
        item.run_status = DefinitionRunStatus.SUCCEEDED.value
        item.next_run_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
        executor.workloads.clear()
        assert AgentOperatorSchedule(session, executor).claim_due(datetime.now(UTC)) == 0
        assert executor.workloads == []
    finally:
        session.close()
        database.dispose()


def test_migration_adds_run_columns(tmp_path: Path) -> None:
    """Upgrade adds the operator run columns on agent_definition_items."""
    database, session, _store, _scanner, _executor = _open(tmp_path)
    try:
        names: set[str] = {column.name for column in AgentDefinitionItemRow.__table__.columns}
        assert {
            "run_status",
            "run_error",
            "ran_at",
            "schedule",
            "next_run_at",
        } <= names
        session.execute(select(AgentDefinitionItemRow.run_status))
    finally:
        session.close()
        database.dispose()


_SCHEMA: str = (
    "from intelegent_data_catalog.agents.dsl import Agent, BuildContext, Describe, Schema\n"
    "Agent(\n"
    '    "sales",\n'
    '    Schema(catalog="iceberg", namespace="sales", data_source="lakehouse"),\n'
    "    operators=[BuildContext(include_lineage=True), Describe()],\n"
    ")\n"
)


class _Steps:
    """Records begin and end for each operator step."""

    def __init__(self) -> None:
        self.steps: list[tuple[str, str | None]] = []

    def begin(self, task_id: str) -> None:
        self.steps.append((task_id, "begin"))

    def end(self, task_id: str, error: str | None) -> None:
        self.steps.append((task_id, error))


class _AliasAgent(ContextBuilder):
    """Describe stand-in that does not touch the catalog table."""

    def get_metadata(self) -> BaseModel:
        return BaseModel()

    def build_context(self, options: object) -> BaseModel:
        del options
        raise AssertionError("build")

    def describe(self) -> str:
        return "alias context"


class _AliasAgents(ContextBuilderProvider):
    """Records whether the table agent is the primary."""

    def __init__(self) -> None:
        self.primary: bool | None = None

    def for_table(self, table_id: object, agent_id: object, *, primary: bool) -> ContextBuilder:
        del table_id, agent_id
        self.primary = primary
        return _AliasAgent()

    def for_dag(self, dag_row_id: object, instance_id: object) -> ContextBuilder:
        del dag_row_id, instance_id
        raise AssertionError("dag")

    def for_instance(self, instance_id: object) -> ContextBuilder:
        del instance_id
        raise AssertionError("instance")

    def for_file(self, agent_id: object, notebook: bool) -> ContextBuilder:
        del agent_id, notebook
        raise AssertionError("file")

    def for_schema(self, agent_id: object) -> ContextBuilder:
        del agent_id
        raise AssertionError("schema")


def test_schema_source_round_trip_and_describe_throttle() -> None:
    """A schema source stores its filters. Describe can skip an existing context."""
    source: Schema = Schema(
        catalog="iceberg",
        namespace="sales",
        data_source="lakehouse",
        include=[" orders "],
        exclude=["returns"],
    )
    assert source.to_ref().include == ["orders"]
    context: _Context = _Context()
    context.context_present = True
    steps: _Steps = _Steps()
    BuildContext(include_lineage=True).execute(context, steps)
    Describe(skip_described=True).execute(context, steps)
    assert context.described is False
    assert context.flags == {"include_lineage": True}
    assert steps.steps == [
        (BUILD_CONTEXT, "begin"),
        (BUILD_CONTEXT, None),
        (DESCRIBE, "begin"),
        (DESCRIBE, None),
    ]
    context.context_present = False
    Describe(skip_described=True).execute(context)
    assert context.described is True
    with pytest.raises(ValidationError, match="listed twice"):
        Schema(catalog="iceberg", namespace="sales", include=["orders", " orders "])
    with pytest.raises(ValidationError, match="cannot be blank"):
        Schema(catalog="iceberg", namespace="sales", exclude=[" "])


def test_schema_operator_is_one_agent(tmp_path: Path) -> None:
    """One schema declaration stays one agent when tables are added or removed."""
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _table(session, "iceberg.sales.returns")
        _put(store, "agents/sales/agent_sales.py", _SCHEMA)
        scanner.scan(datetime.now(UTC))
        keys: dict[str, AgentDefinitionItemRow] = {
            item.item_key: item for item in session.scalars(select(AgentDefinitionItemRow))
        }
        assert set(keys) == {"sales"}
        assert keys["sales"].status == DefinitionItemStatus.APPLIED.value
        schema_agent: AgentRow | None = session.get(AgentRow, keys["sales"].agent_id)
        assert schema_agent is not None
        assert schema_agent.name == "sales"
        assert schema_agent.kind == AgentKind.SCHEMA.value
        assert [item.item_key for item in executor.workloads] == ["sales"]
        table: CatalogTableRow | None = session.scalar(
            select(CatalogTableRow).where(CatalogTableRow.name == "orders"),
        )
        assert table is not None and table.agent_id is None
        executor.workloads.clear()
        scanner.scan(datetime.now(UTC))
        assert executor.workloads == []
        _table(session, "iceberg.sales.payments")
        scanner.scan(datetime.now(UTC))
        assert executor.workloads == []
        assert set(session.scalars(select(AgentDefinitionItemRow.item_key))) == {"sales"}
        gone: CatalogTableRow | None = session.scalar(
            select(CatalogTableRow).where(CatalogTableRow.name == "returns"),
        )
        assert gone is not None
        session.delete(gone)
        session.commit()
        scanner.scan(datetime.now(UTC))
        assert set(session.scalars(select(AgentDefinitionItemRow.item_key))) == {"sales"}
        _put(store, "agents/sales/agent_sales.py", "pass\n")
        scanner.scan(datetime.now(UTC))
        assert list(session.scalars(select(AgentDefinitionItemRow))) == []
        assert list(session.scalars(select(AgentRow))) == []
    finally:
        session.close()
        database.dispose()


def test_schema_operator_stays_unresolved_until_a_table_exists(tmp_path: Path) -> None:
    """An empty schema is unresolved, then a later scan creates the one agent."""
    database, session, store, scanner, executor = _open(tmp_path)
    try:
        _put(store, "agents/sales/agent_sales.py", _SCHEMA)
        scanner.scan(datetime.now(UTC))
        parent: AgentDefinitionItemRow | None = session.scalar(select(AgentDefinitionItemRow))
        assert parent is not None
        assert parent.status == DefinitionItemStatus.UNRESOLVED.value
        assert parent.error == "data source not found: lakehouse"
        assert executor.workloads == []
        _table(session, "iceberg.sales.orders")
        scanner.scan(datetime.now(UTC))
        session.expire_all()
        parent_again: AgentDefinitionItemRow | None = session.scalar(
            select(AgentDefinitionItemRow).where(AgentDefinitionItemRow.item_key == "sales"),
        )
        assert parent_again is not None
        assert parent_again.status == DefinitionItemStatus.APPLIED.value
        assert parent_again.agent_id is not None
        assert [item.item_key for item in executor.workloads] == ["sales"]
        assert set(session.scalars(select(AgentDefinitionItemRow.item_key))) == {"sales"}
    finally:
        session.close()
        database.dispose()


def test_schema_include_and_exclude_limit_children(tmp_path: Path) -> None:
    """include and exclude choose tables, and a later exclude retires a child."""
    database, session, store, scanner, _executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        _table(session, "iceberg.sales.returns")
        included: str = (
            "from intelegent_data_catalog.agents.dsl import Agent, Schema\n"
            "Agent(\n"
            '    "sales",\n'
            "    Schema(\n"
            '        catalog="iceberg",\n'
            '        namespace="sales",\n'
            '        data_source="lakehouse",\n'
            '        include=["orders"],\n'
            "    ),\n"
            ")\n"
        )
        _put(store, "agents/sales/agent_sales.py", included)
        scanner.scan(datetime.now(UTC))
        keys: set[str] = set(session.scalars(select(AgentDefinitionItemRow.item_key)))
        assert keys == {"sales"}
        kept: AgentRow | None = session.scalar(select(AgentRow))
        assert kept is not None and kept.properties["include"] == ["orders"]
        _put(store, "agents/sales/agent_sales.py", _SCHEMA)
        scanner.scan(datetime.now(UTC))
        session.expire_all()
        both: set[str] = set(session.scalars(select(AgentDefinitionItemRow.item_key)))
        assert both == {"sales"}
        widened: AgentRow | None = session.scalar(select(AgentRow))
        assert widened is not None and widened.id == kept.id
        assert widened.properties["include"] is None
        excluded: str = included.replace('include=["orders"]', 'exclude=["returns"]')
        _put(store, "agents/sales/agent_sales.py", excluded)
        scanner.scan(datetime.now(UTC))
        session.expire_all()
        after: AgentRow | None = session.scalar(select(AgentRow))
        assert after is not None and after.id == kept.id
        assert after.properties["exclude"] == ["returns"]
    finally:
        session.close()
        database.dispose()


def test_schema_agent_stays_beside_an_explicit_table(tmp_path: Path) -> None:
    """An explicit table agent keeps the catalog row. The schema agent does not."""
    database, session, store, scanner, _executor = _open(tmp_path)
    try:
        _table(session, "iceberg.sales.orders")
        both: str = (
            "from intelegent_data_catalog.agents.dsl import Agent, BuildContext, Describe, "
            "IcebergTable, Schema\n"
            'Agent("orders", IcebergTable(source_key="iceberg.sales.orders", '
            'data_source="lakehouse"), operators=[BuildContext(), Describe()])\n'
            + _SCHEMA
        )
        _put(store, "agents/sales/agent_sales.py", both)
        scanner.scan(datetime.now(UTC))
        explicit: AgentDefinitionItemRow | None = session.scalar(
            select(AgentDefinitionItemRow).where(AgentDefinitionItemRow.item_key == "orders"),
        )
        schema_item: AgentDefinitionItemRow | None = session.scalar(
            select(AgentDefinitionItemRow).where(AgentDefinitionItemRow.item_key == "sales"),
        )
        table: CatalogTableRow | None = session.scalar(select(CatalogTableRow))
        assert explicit is not None and schema_item is not None and table is not None
        assert explicit.agent_id is not None and schema_item.agent_id is not None
        assert explicit.agent_id != schema_item.agent_id
        assert table.agent_id == explicit.agent_id
        schema_agent: AgentRow | None = session.get(AgentRow, schema_item.agent_id)
        assert schema_agent is not None
        assert schema_agent.kind == AgentKind.SCHEMA.value
        assert "alias_of" not in schema_agent.properties
        _put(store, "agents/sales/agent_sales.py", _SCHEMA)
        scanner.scan(datetime.now(UTC))
        session.expire_all()
        assert session.get(AgentDefinitionItemRow, explicit.id) is None
        kept: AgentDefinitionItemRow | None = session.scalar(
            select(AgentDefinitionItemRow).where(AgentDefinitionItemRow.item_key == "sales"),
        )
        assert kept is not None and kept.agent_id == schema_agent.id
        session.refresh(table)
        assert table.agent_id is None
    finally:
        session.close()
        database.dispose()


def _table_operator() -> IcebergTable:
    operator: IcebergTable = IcebergTable(source_key="iceberg.sales.orders")
    return operator


def _open(
    tmp_path: Path,
) -> tuple[CatalogDatabase, Session, MemoryObjectStore, AgentDefinitionScanner, _RecordingExecutor]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    session: Session = database.session()
    store: MemoryObjectStore = MemoryObjectStore()
    settings: AgentDefinitionsSettings = AgentDefinitionsSettings(enabled=True, prefix=_PREFIX)
    executor: _RecordingExecutor = _RecordingExecutor()
    scanner: AgentDefinitionScanner = AgentDefinitionScanner(
        session,
        store,
        settings,
        logging.getLogger("test.agent_definitions"),
        executor,
    )
    return database, session, store, scanner, executor


def _put(store: MemoryObjectStore, key: str, source: str) -> None:
    store.put(f"{_PREFIX}{key}", source.encode(), "text/x-python")


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
