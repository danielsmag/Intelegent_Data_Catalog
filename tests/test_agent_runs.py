"""Manual agent context builds still record a run."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.models import AgentContextOptions
from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.api.platform import RequestPlatform
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.jobs.catalog import CatalogAgentBuild
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    DefinitionRunStatus,
    OperatorRunTrigger,
    OperatorTaskState,
)
from intelegent_data_catalog.persistence.models import (
    AgentOperatorRunRow,
    AgentOperatorRunTaskRow,
    AgentRow,
)
from tests.test_agent_definitions import _ORDERS, _open, _put, _send, _table


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


def test_run_api_returns_the_manual_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runs route lists the manual build without a log body on the summary."""
    database, session, _store, _executor = _scanned(tmp_path)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))

    def build(self: RequestPlatform, agent_id: UUID, options: AgentContextOptions) -> None:
        del self, agent_id, options

    monkeypatch.setattr(RequestPlatform, "build_agent_context", build)
    agent_id: str = ""
    run_id: str = ""
    try:
        agent: AgentRow | None = session.scalar(select(AgentRow).where(AgentRow.name == "orders"))
        assert agent is not None
        CatalogAgentBuild(container).run(agent.id, AgentContextOptions())
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
        assert row["trigger"] == "manual"
        assert "item_id" not in row
        detail: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/agents/{agent_id}/runs/{run_id}/tasks/build_context",
        )
        assert detail.status_code == 200
        payload: dict[str, object] = detail.json()
        assert payload["state"] == "succeeded"
    finally:
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
