"""Sync schedules, the beat claim, and one-running-job builds."""

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from dependency_injector import providers
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.jobs.catalog_jobs import CatalogJobs, CatalogJobStore
from intelegent_data_catalog.jobs.context_refresh import ContextRefreshService
from intelegent_data_catalog.jobs.models import KnowledgeBuildJobRead
from intelegent_data_catalog.jobs.publisher import SyncJobPublisher
from intelegent_data_catalog.jobs.schedules import SyncScheduleService
from intelegent_data_catalog.knowledge.models import TableContextOptions
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import SyncRunStatus
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import (
    CatalogJobRow,
    EntityConfigurationRow,
    MetadataSyncRunRow,
    SyncScheduleRow,
)


class _RecordingBuilds:
    """Records build enqueues and does not run them."""

    def __init__(self) -> None:
        self.job_ids: list[UUID] = []

    def enqueue_build(self, job_id: UUID) -> None:
        self.job_ids.append(job_id)


class _RecordingSyncs:
    """Records sync enqueues and does not run them."""

    def __init__(self) -> None:
        self.bindings: list[UUID] = []
        self.instances: list[UUID] = []

    def enqueue_trino_sync(self, source_binding_id: UUID) -> None:
        self.bindings.append(source_binding_id)

    def enqueue_airflow_sync(self, instance_id: UUID) -> None:
        self.instances.append(instance_id)


def test_schedule_upsert_and_delete(tmp_path: Path) -> None:
    """A Trino binding and an Airflow instance can store, replace, and drop a schedule."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        binding_id: str = _trino_binding(app)
        missing: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/source-bindings/{binding_id}/schedule",
        )
        assert missing.status_code == 404
        rejected: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/source-bindings/{binding_id}/schedule",
            {"interval_seconds": 0, "enabled": True},
        )
        assert rejected.status_code == 422
        fifth: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/source-bindings/{binding_id}/schedule",
            {"interval_seconds": 12, "enabled": True},
        )
        assert fifth.status_code == 200
        assert _object(fifth)["interval_seconds"] == 12
        created: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/source-bindings/{binding_id}/schedule",
            {"interval_seconds": 900, "enabled": True},
        )
        assert created.status_code == 200
        body: dict[str, object] = _object(created)
        assert body["source_engine_binding_id"] == binding_id
        assert body["enabled"] is True
        assert body["interval_seconds"] == 900
        replaced: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/source-bindings/{binding_id}/schedule",
            {"interval_seconds": 120, "enabled": False},
        )
        assert replaced.status_code == 200
        assert _object(replaced)["interval_seconds"] == 120
        assert _object(replaced)["id"] == body["id"]
        spark_id: str = _spark_binding(app)
        spark: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/source-bindings/{spark_id}/schedule",
            {"interval_seconds": 900, "enabled": True},
        )
        assert spark.status_code == 422
        instance: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/airflow/instances",
            {
                "name": "Local",
                "base_url": "http://airflow.test",
                "username": "admin",
                "password": "s3cret-airflow",
                "verify_tls": True,
            },
        )
        assert instance.status_code == 201
        instance_id: str = _text(_object(instance), "id")
        airflow: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/airflow/instances/{instance_id}/schedule",
            {"interval_seconds": 600, "enabled": True},
        )
        assert airflow.status_code == 200
        assert _object(airflow)["airflow_instance_id"] == instance_id
        removed: httpx.Response = _send(
            app,
            "DELETE",
            f"/api/v1/source-bindings/{binding_id}/schedule",
        )
        assert removed.status_code == 204
        gone: httpx.Response = _send(app, "GET", f"/api/v1/source-bindings/{binding_id}/schedule")
        assert gone.status_code == 404
    finally:
        database.dispose()


def test_dispatcher_claims_due_schedule_and_skips_running_sync(tmp_path: Path) -> None:
    """A due binding is enqueued once. A running sync leaves the schedule due."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        binding_id: str = _trino_binding(app)
        saved: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/source-bindings/{binding_id}/schedule",
            {"interval_seconds": 900, "enabled": True},
        )
        assert saved.status_code == 200
        publisher: _RecordingSyncs = _RecordingSyncs()
        claimed: int = _claim(database, publisher)
        assert claimed == 1
        assert publisher.bindings == [UUID(binding_id)]
        again: int = _claim(database, publisher)
        assert again == 0
        assert publisher.bindings == [UUID(binding_id)]
        _mark_due_and_running(database, UUID(binding_id))
        skipped: int = _claim(database, publisher)
        assert skipped == 0
        assert publisher.bindings == [UUID(binding_id)]
    finally:
        database.dispose()


def test_second_build_start_returns_the_running_job(tmp_path: Path) -> None:
    """A second start for the same table joins the running job and does not enqueue again."""
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    publisher: _RecordingBuilds = _RecordingBuilds()
    jobs: CatalogJobs = CatalogJobs(database, publisher)
    try:
        subject_id: UUID = uuid4()
        options: TableContextOptions = TableContextOptions()
        first: KnowledgeBuildJobRead = jobs.start_knowledge(subject_id, "orders", options)
        second: KnowledgeBuildJobRead = jobs.start_knowledge(subject_id, "orders", options)
        assert second.id == first.id
        assert second.status == "running"
        assert publisher.job_ids == [first.id]
    finally:
        database.dispose()


def test_entity_configuration_is_stored_in_the_database(tmp_path: Path) -> None:
    """A space configuration defaults, then Save replaces the stored row."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        space: dict[str, object] = _object(
            _send(app, "POST", "/api/v1/spaces", {"name": "Commerce"}),
        )
        space_id: str = _text(space, "id")
        loaded: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/spaces/{space_id}/configuration",
        )
        assert loaded.status_code == 200
        defaults: dict[str, object] = _object(loaded)
        assert defaults["interval_seconds"] == 600
        assert defaults["enabled"] is True
        assert isinstance(defaults["next_run_at"], str)
        saved: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/spaces/{space_id}/configuration",
            {
                "interval_seconds": 12,
                "enabled": True,
                "options": {
                    "include_scope_knowledge": True,
                    "include_previous": False,
                    "include_summary": True,
                },
            },
        )
        assert saved.status_code == 200
        stored: dict[str, object] = _object(saved)
        assert stored["interval_seconds"] == 12
        options: object = stored["options"]
        assert isinstance(options, dict)
        assert options["include_previous"] is False
        again: dict[str, object] = _object(
            _send(app, "GET", f"/api/v1/spaces/{space_id}/configuration"),
        )
        assert again["interval_seconds"] == 12
        rejected: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/spaces/{space_id}/configuration",
            {
                "interval_seconds": 12,
                "enabled": True,
                "options": {"not_a_setting": True},
            },
        )
        assert rejected.status_code == 422
        missing: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/spaces/{uuid4()}/configuration",
        )
        assert missing.status_code == 404
    finally:
        database.dispose()


def test_due_table_configuration_queues_one_context_rebuild(tmp_path: Path) -> None:
    """Beat rebuilds a due table once, waits out a running job, then rebuilds again."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        table_id: str = _catalog_table(app)
        saved: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/tables/{table_id}/configuration",
            {
                "interval_seconds": 600,
                "enabled": True,
                "options": {
                    "include_graph": True,
                    "include_lineage": False,
                    "include_data_profile": False,
                },
            },
        )
        assert saved.status_code == 200
        publisher: _RecordingBuilds = _RecordingBuilds()
        jobs: CatalogJobs = CatalogJobs(database, publisher)
        fresh: int = _claim_context(database, jobs)
        assert fresh == 0
        _age_table_configuration(database, UUID(table_id))
        claimed: int = _claim_context(database, jobs)
        assert claimed == 1
        assert len(publisher.job_ids) == 1
        _finish_job(database, publisher.job_ids[0])
        waiting: int = _claim_context(database, jobs)
        assert waiting == 0
        _age_job(database, publisher.job_ids[0])
        again: int = _claim_context(database, jobs)
        assert again == 1
        assert len(publisher.job_ids) == 2
        _age_job(database, publisher.job_ids[1])
        while_running: int = _claim_context(database, jobs)
        assert while_running == 0
        assert len(publisher.job_ids) == 2
        _finish_job(database, publisher.job_ids[1])
        _age_job(database, publisher.job_ids[1])
        stored: CatalogJobRow = _job(database, publisher.job_ids[0])
        assert stored.kind == "knowledge"
        assert stored.subject_id == UUID(table_id)
        assert stored.options["include_lineage"] is False
        assert stored.options["include_data_profile"] is False
        disabled: httpx.Response = _send(
            app,
            "PUT",
            f"/api/v1/tables/{table_id}/configuration",
            {
                "interval_seconds": 600,
                "enabled": False,
                "options": {
                    "include_graph": True,
                    "include_lineage": False,
                    "include_data_profile": False,
                },
            },
        )
        assert disabled.status_code == 200
        assert _object(disabled)["next_run_at"] is None
        _age_table_configuration(database, UUID(table_id))
        skipped: int = _claim_context(database, jobs)
        assert skipped == 0
        assert len(publisher.job_ids) == 2
    finally:
        database.dispose()


def _claim_context(database: CatalogDatabase, jobs: CatalogJobs) -> int:
    session: Session = database.session()
    try:
        service: ContextRefreshService = ContextRefreshService(session, jobs)
        return service.claim_due(utc_now())
    finally:
        session.close()


def _age_table_configuration(database: CatalogDatabase, table_id: UUID) -> None:
    session: Session = database.session()
    try:
        row: EntityConfigurationRow | None = session.scalar(
            select(EntityConfigurationRow).where(
                EntityConfigurationRow.catalog_table_id == table_id,
            ),
        )
        assert row is not None
        row.created_at = utc_now() - timedelta(hours=1)
        session.commit()
    finally:
        session.close()


def _age_job(database: CatalogDatabase, job_id: UUID) -> None:
    session: Session = database.session()
    try:
        row: CatalogJobRow | None = session.get(CatalogJobRow, job_id)
        assert row is not None
        row.created_at = utc_now() - timedelta(hours=1)
        session.commit()
    finally:
        session.close()


def _finish_job(database: CatalogDatabase, job_id: UUID) -> None:
    CatalogJobStore(database).complete(job_id, "succeeded", None)


def _job(database: CatalogDatabase, job_id: UUID) -> CatalogJobRow:
    session: Session = database.session()
    try:
        row: CatalogJobRow | None = session.get(CatalogJobRow, job_id)
        assert row is not None
        session.expunge(row)
        return row
    finally:
        session.close()


def _catalog_table(app: FastAPI) -> str:
    space: dict[str, object] = _object(
        _send(app, "POST", "/api/v1/spaces", {"name": "Commerce"}),
    )
    scope: dict[str, object] = _object(
        _send(
            app,
            "POST",
            f"/api/v1/spaces/{_text(space, 'id')}/scopes",
            {"name": "Ordering"},
        ),
    )
    source: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/data-sources",
            {"name": "Lake", "type": "ICEBERG_REST"},
        ),
    )
    table: dict[str, object] = _object(
        _send(
            app,
            "POST",
            f"/api/v1/scopes/{_text(scope, 'id')}/tables",
            {
                "data_source_id": _text(source, "id"),
                "name": "Orders",
                "table_type": "TABLE",
                "physical_catalog": "polaris",
                "physical_namespace": "sales",
                "physical_table": "Orders",
                "source_ref": {"catalog": "polaris", "namespace": "sales", "table": "Orders"},
            },
        ),
    )
    return _text(table, "id")


def _claim(database: CatalogDatabase, publisher: SyncJobPublisher) -> int:
    session: Session = database.session()
    try:
        service: SyncScheduleService = SyncScheduleService(session)
        return service.claim_due(utc_now(), publisher)
    finally:
        session.close()


def _mark_due_and_running(database: CatalogDatabase, binding_id: UUID) -> None:
    session: Session = database.session()
    try:
        schedule: SyncScheduleRow | None = session.scalar(
            select(SyncScheduleRow).where(SyncScheduleRow.source_engine_binding_id == binding_id),
        )
        assert schedule is not None
        schedule.next_run_at = utc_now() - timedelta(seconds=5)
        session.add(
            MetadataSyncRunRow(
                id=uuid4(),
                source_engine_binding_id=binding_id,
                started_at=utc_now(),
                finished_at=None,
                status=SyncRunStatus.RUNNING.value,
                scan_scope={},
                counts={},
                sanitized_error=None,
                checkpoint=None,
            ),
        )
        session.commit()
    finally:
        session.close()


def _trino_binding(app: FastAPI) -> str:
    source: dict[str, object] = _object(
        _send(app, "POST", "/api/v1/data-sources", {"name": "Lake", "type": "ICEBERG_REST"}),
    )
    engine: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/engine-connections",
            {"name": "Trino", "engine_type": "TRINO", "endpoint": "http://trino:8080"},
        ),
    )
    binding: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/source-bindings",
            {
                "data_source_id": _text(source, "id"),
                "engine_connection_id": _text(engine, "id"),
                "binding_key": "lakehouse",
            },
        ),
    )
    return _text(binding, "id")


def _spark_binding(app: FastAPI) -> str:
    source: dict[str, object] = _object(
        _send(app, "POST", "/api/v1/data-sources", {"name": "Warehouse", "type": "ICEBERG_REST"}),
    )
    engine: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/engine-connections",
            {"name": "Spark", "engine_type": "SPARK_SQL", "endpoint": "http://spark:8091"},
        ),
    )
    binding: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/source-bindings",
            {
                "data_source_id": _text(source, "id"),
                "engine_connection_id": _text(engine, "id"),
                "binding_key": "sparkhouse",
            },
        ),
    )
    return _text(binding, "id")


def _app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    return create_app(container), database


def _send(
    app: FastAPI,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> httpx.Response:
    return asyncio.run(_request(app, method, path, body))


def _object(response: httpx.Response) -> dict[str, object]:
    payload: object = response.json()
    assert isinstance(payload, dict)
    return payload


def _text(body: dict[str, object], key: str) -> str:
    value: object = body[key]
    assert isinstance(value, str)
    return value


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response: httpx.Response = await client.request(method, path, json=body)
        return response
