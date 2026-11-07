"""Agent operator run history: tasks, logs, and the read API."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.execution.runtime import OperatorRunner, PlatformOperatorContext
from intelegent_data_catalog.agents.execution.workload import OperatorWorkload
from intelegent_data_catalog.agents.models import AgentContextOptions
from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.jobs.catalog import CatalogAgentBuild
from intelegent_data_catalog.jobs.trace import publish_build_step
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    DefinitionRunStatus,
    OperatorRunTrigger,
    OperatorTaskState,
)
from intelegent_data_catalog.persistence.models import (
    AgentDefinitionItemRow,
    AgentOperatorRunRow,
    AgentOperatorRunTaskRow,
    AgentRow,
)
from intelegent_data_catalog.schedual.operators import AgentOperatorSchedule
from tests.test_agent_definitions import _ORDERS, _open, _put, _send, _table


def test_runner_stores_succeeded_tasks_log_and_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished operator run keeps both tasks, the log line, and the trace step."""
    database, session, _store, executor = _scanned(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    described: list[str] = []

    def build(self: PlatformOperatorContext, flags: dict[str, bool]) -> None:
        del flags
        self.logger.warning("context built")
        publish_build_step("agent", "tool_call", "read_catalog tables")

    def describe(self: PlatformOperatorContext) -> None:
        del self
        described.append("describe")

    monkeypatch.setattr(PlatformOperatorContext, "build_context", build)
    monkeypatch.setattr(PlatformOperatorContext, "describe", describe)
    try:
        workload: OperatorWorkload = executor.workloads[0]
        OperatorRunner(container, logging.getLogger("test.runner")).run(workload)
        assert described == ["describe"]
        item: AgentDefinitionItemRow | None = session.get(AgentDefinitionItemRow, workload.item_id)
        assert item is not None
        session.refresh(item)
        assert item.run_status == DefinitionRunStatus.SUCCEEDED.value
        runs: list[AgentOperatorRunRow] = _runs(database)
        assert len(runs) == 1
        run: AgentOperatorRunRow = runs[0]
        assert run.status == DefinitionRunStatus.SUCCEEDED.value
        assert run.trigger == OperatorRunTrigger.APPLY.value
        assert run.try_number == 1
        assert run.operator_type is None
        tasks: dict[str, AgentOperatorRunTaskRow] = _tasks(database, run.id)
        assert tasks["build_context"].state == OperatorTaskState.SUCCEEDED.value
        assert tasks["describe"].state == OperatorTaskState.SUCCEEDED.value
        assert "context built" in tasks["build_context"].log
        steps: list[object] = list(tasks["build_context"].events)
        assert any(_step(event) == "tool_call" for event in steps)
    finally:
        session.close()
        database.dispose()


def test_build_context_failure_skips_describe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed build marks describe upstream_failed and does not call it."""
    database, session, _store, executor = _scanned(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    described: list[str] = []

    def build(self: PlatformOperatorContext, flags: dict[str, bool]) -> None:
        del self, flags
        raise CatalogError("nope")

    def describe(self: PlatformOperatorContext) -> None:
        del self
        described.append("describe")

    monkeypatch.setattr(PlatformOperatorContext, "build_context", build)
    monkeypatch.setattr(PlatformOperatorContext, "describe", describe)
    try:
        workload: OperatorWorkload = executor.workloads[0]
        OperatorRunner(container, logging.getLogger("test.runner")).run(workload)
        assert described == []
        item: AgentDefinitionItemRow | None = session.get(AgentDefinitionItemRow, workload.item_id)
        assert item is not None
        session.refresh(item)
        assert item.run_status == DefinitionRunStatus.FAILED.value
        assert item.run_error == "nope"
        runs: list[AgentOperatorRunRow] = _runs(database)
        assert len(runs) == 1
        assert runs[0].status == DefinitionRunStatus.FAILED.value
        assert runs[0].error == "nope"
        tasks: dict[str, AgentOperatorRunTaskRow] = _tasks(database, runs[0].id)
        assert tasks["build_context"].state == OperatorTaskState.FAILED.value
        assert tasks["build_context"].error == "nope"
        assert tasks["describe"].state == OperatorTaskState.UPSTREAM_FAILED.value
    finally:
        session.close()
        database.dispose()


def test_manual_build_skips_describe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent page build records build_context and leaves describe skipped."""
    database, session, _store, _executor = _scanned(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))

    def build(self: RequestPlatform, agent_id: UUID, options: AgentContextOptions) -> None:
        del self, agent_id, options

    monkeypatch.setattr(RequestPlatform, "build_agent_context", build)
    try:
        agent: AgentRow | None = session.scalar(select(AgentRow).where(AgentRow.name == "orders"))
        assert agent is not None
        CatalogAgentBuild(container).run(agent.id, AgentContextOptions())
        runs: list[AgentOperatorRunRow] = _runs(database)
        assert len(runs) == 1
        assert runs[0].trigger == OperatorRunTrigger.MANUAL.value
        assert runs[0].status == DefinitionRunStatus.SUCCEEDED.value
        tasks: dict[str, AgentOperatorRunTaskRow] = _tasks(database, runs[0].id)
        assert tasks["build_context"].state == OperatorTaskState.SUCCEEDED.value
        assert tasks["describe"].state == OperatorTaskState.SKIPPED.value
    finally:
        session.close()
        database.dispose()


def test_run_api_returns_the_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List omits the log body. The task route returns it."""
    database, session, _store, executor = _scanned(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))

    def build(self: PlatformOperatorContext, flags: dict[str, bool]) -> None:
        del flags
        self.logger.warning("context built")

    def describe(self: PlatformOperatorContext) -> None:
        del self

    monkeypatch.setattr(PlatformOperatorContext, "build_context", build)
    monkeypatch.setattr(PlatformOperatorContext, "describe", describe)
    agent_id: str = ""
    run_id: str = ""
    try:
        OperatorRunner(container, logging.getLogger("test.runner")).run(executor.workloads[0])
        agent: AgentRow | None = session.scalar(select(AgentRow).where(AgentRow.name == "orders"))
        assert agent is not None
        agent_id = str(agent.id)
        stored: list[AgentOperatorRunRow] = _runs(database)
        assert len(stored) == 1
        run_id = str(stored[0].id)
    finally:
        session.close()
    app: FastAPI = create_app(container)
    try:
        listed: httpx.Response = _send(app, "GET", f"/api/v1/agents/{agent_id}/runs")
        assert listed.status_code == 200
        body: list[object] = listed.json()
        assert len(body) == 1
        row: object = body[0]
        assert isinstance(row, dict)
        assert row["status"] == "succeeded"
        assert row["trigger"] == "apply"
        task_rows: object = row["tasks"]
        assert isinstance(task_rows, list)
        first: object = task_rows[0]
        assert isinstance(first, dict)
        assert first["task_id"] == "build_context"
        assert "log" not in first
        detail: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/agents/{agent_id}/runs/{run_id}/tasks/build_context",
        )
        assert detail.status_code == 200
        payload: dict[str, object] = detail.json()
        assert "context built" in str(payload["log"])
        assert payload["state"] == "succeeded"
    finally:
        database.dispose()


def test_schedule_workload_uses_schedule_trigger(tmp_path: Path) -> None:
    """A cron claim marks the workload as a schedule run."""
    database, session, _store, executor = _scanned(tmp_path)
    try:
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
        assert executor.workloads[0].trigger == OperatorRunTrigger.SCHEDULE
    finally:
        session.close()
        database.dispose()


def test_migration_creates_run_tables(tmp_path: Path) -> None:
    """Upgrade creates the run history tables."""
    database, session, _store, _executor = _scanned(tmp_path)
    try:
        session.execute(select(AgentOperatorRunRow.id))
        session.execute(select(AgentOperatorRunTaskRow.id))
    finally:
        session.close()
        database.dispose()


def _scanned(
    tmp_path: Path,
) -> tuple[CatalogDatabase, Session, object, object]:
    database, session, store, scanner, executor = _open(tmp_path)
    _table(session, "iceberg.sales.orders")
    _put(store, "agents/sales/agent_orders.py", _ORDERS)
    scanner.scan(datetime.now(UTC))
    return database, session, store, executor


def _runs(database: CatalogDatabase) -> list[AgentOperatorRunRow]:
    session: Session = database.session()
    try:
        return list(
            session.scalars(
                select(AgentOperatorRunRow).order_by(AgentOperatorRunRow.queued_at.desc()),
            ),
        )
    finally:
        session.close()


def _tasks(database: CatalogDatabase, run_id: UUID) -> dict[str, AgentOperatorRunTaskRow]:
    session: Session = database.session()
    try:
        rows: list[AgentOperatorRunTaskRow] = list(
            session.scalars(
                select(AgentOperatorRunTaskRow).where(AgentOperatorRunTaskRow.run_id == run_id),
            ),
        )
    finally:
        session.close()
    return {row.task_id: row for row in rows}


def _step(event: object) -> str:
    if isinstance(event, dict):
        step: object = event.get("step")
        if isinstance(step, str):
            return step
    return ""
