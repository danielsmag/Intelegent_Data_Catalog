"""Airflow instance HTTP: hidden password, sync snapshot, manual links, cascade delete."""

import asyncio
import base64
import json
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx
from dependency_injector import providers
from fastapi import FastAPI
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import AssetKind
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import AirflowInstanceRow, AssetRow

_PASSWORD: str = "s3cret-airflow"
_DAG_ID: str = "append_sales_customer"


def test_instance_crud_hides_the_password(tmp_path: Path) -> None:
    """Reads omit the password. A duplicate slug and an embedded URL are rejected."""
    app: FastAPI
    database: CatalogDatabase
    http: httpx.Client
    app, database, http = _app(tmp_path, _airflow)
    try:
        created: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/airflow/instances",
            _instance("Local"),
        )
        assert created.status_code == 201
        body: dict[str, object] = _object(created)
        assert "password" not in body
        assert _PASSWORD not in created.text
        assert body["has_credentials"] is True
        assert body["slug"] == "local"
        assert body["dag_count"] == 0
        asset_id: str = _text(body, "asset_id")
        session: Session = database.session()
        try:
            instance_id: UUID = UUID(str(body["id"]))
            linked: AirflowInstanceRow | None = session.get(AirflowInstanceRow, instance_id)
            assert linked is not None
            assert linked.asset_id is not None
            assert str(linked.asset_id) == asset_id
            stored: AssetRow | None = session.get(AssetRow, linked.asset_id)
            assert stored is not None
            assert stored.kind == AssetKind.AIRFLOW_INSTANCE.value
        finally:
            session.close()
        duplicate: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/airflow/instances",
            _instance("Local", "http://other.test"),
        )
        assert duplicate.status_code == 409
        embedded: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/airflow/instances",
            _instance("Embedded", "http://admin:s3cret-url@airflow.test"),
        )
        assert embedded.status_code == 422
        assert "s3cret-url" not in embedded.text
        tested: httpx.Response = _send(
            app,
            "POST",
            "/api/v1/airflow/instances/test",
            {
                "base_url": "http://airflow.test",
                "username": "admin",
                "password": _PASSWORD,
                "verify_tls": True,
            },
        )
        assert tested.status_code == 200
        report: dict[str, object] = _object(tested)
        assert report["ok"] is True
        assert report["version"] == "3.3.2"
        assert _PASSWORD not in tested.text
        removed: httpx.Response = _send(app, "DELETE", f"/api/v1/airflow/instances/{body['id']}")
        assert removed.status_code == 204
        after: Session = database.session()
        try:
            assert after.get(AssetRow, UUID(asset_id)) is None
        finally:
            after.close()
    finally:
        http.close()
        database.dispose()


def test_sync_maps_tables_and_keeps_manual_links(tmp_path: Path) -> None:
    """Sync stores the DAG, source match, and asset output. Manual links survive a resync."""
    app: FastAPI
    database: CatalogDatabase
    http: httpx.Client
    app, database, http = _app(tmp_path, _airflow)
    try:
        instance_id: str = _text(
            _object(_send(app, "POST", "/api/v1/airflow/instances", _instance("Local"))),
            "id",
        )
        customers_id: str = _catalog_table(app, "customers")
        orders_id: str = _catalog_table(app, "orders")
        synced: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/airflow/instances/{instance_id}/sync",
        )
        assert synced.status_code == 200
        run: dict[str, object] = _object(synced)
        assert run["status"] == "SUCCEEDED"
        counts: dict[str, object] = _object_field(run, "counts")
        assert counts["dags_seen"] == 1
        assert counts["mappings"] == 2
        detail: dict[str, object] = _object(
            _send(app, "GET", f"/api/v1/airflow/instances/{instance_id}/dags/{_DAG_ID}"),
        )
        assert detail["source_code"] == "target = 'sales.customers'\n"
        links: dict[str, dict[str, object]] = {
            _text(item, "catalog_table_id"): item for item in _objects(detail, "tables")
        }
        assert links[customers_id]["origin"] == "SOURCE_MATCH"
        assert links[customers_id]["direction"] == "UNKNOWN"
        assert links[customers_id]["evidence"] == "sales.customers"
        assert links[orders_id]["origin"] == "ASSET"
        assert links[orders_id]["direction"] == "OUTPUT"
        manual: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/airflow/instances/{instance_id}/dags/{_DAG_ID}/tables",
            {"catalog_table_id": customers_id, "direction": "INPUT"},
        )
        assert manual.status_code == 201
        assert _object(manual)["origin"] == "MANUAL"
        again: httpx.Response = _send(
            app,
            "POST",
            f"/api/v1/airflow/instances/{instance_id}/sync",
        )
        assert again.status_code == 200
        refreshed: dict[str, object] = _object(
            _send(app, "GET", f"/api/v1/airflow/instances/{instance_id}/dags/{_DAG_ID}"),
        )
        origins: dict[str, set[str]] = {}
        row: dict[str, object]
        for row in _objects(refreshed, "tables"):
            table_id: str = _text(row, "catalog_table_id")
            origins.setdefault(table_id, set()).add(_text(row, "origin"))
        assert origins[customers_id] == {"SOURCE_MATCH", "MANUAL"}
        assert origins[orders_id] == {"ASSET"}
        blocked: httpx.Response = _send(
            app,
            "DELETE",
            f"/api/v1/airflow/instances/{instance_id}/dags/{_DAG_ID}/tables/{orders_id}",
        )
        assert blocked.status_code == 409
        removed: httpx.Response = _send(
            app,
            "DELETE",
            f"/api/v1/airflow/instances/{instance_id}/dags/{_DAG_ID}/tables/{customers_id}",
        )
        assert removed.status_code == 204
        after_delete: dict[str, object] = _object(
            _send(app, "GET", f"/api/v1/airflow/instances/{instance_id}/dags/{_DAG_ID}"),
        )
        remaining: set[str] = {_text(item, "origin") for item in _objects(after_delete, "tables")}
        assert "MANUAL" not in remaining
        assert "SOURCE_MATCH" in remaining
        reverse: httpx.Response = _send(app, "GET", f"/api/v1/tables/{orders_id}/airflow-dags")
        assert reverse.status_code == 200
        reverse_body: object = reverse.json()
        assert isinstance(reverse_body, list)
        assert len(reverse_body) == 1
        deleted: httpx.Response = _send(app, "DELETE", f"/api/v1/airflow/instances/{instance_id}")
        assert deleted.status_code == 204
        missing: httpx.Response = _send(app, "GET", f"/api/v1/airflow/instances/{instance_id}")
        assert missing.status_code == 404
        missing_dag: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/airflow/instances/{instance_id}/dags/{_DAG_ID}",
        )
        assert missing_dag.status_code == 404
    finally:
        http.close()
        database.dispose()


def _instance(name: str, base_url: str = "http://airflow.test") -> dict[str, object]:
    return {
        "name": name,
        "base_url": base_url,
        "username": "admin",
        "password": _PASSWORD,
        "verify_tls": True,
    }


def _catalog_table(app: FastAPI, table: str) -> str:
    source: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/data-sources",
            {
                "name": f"Lake {table}",
                "type": "ICEBERG_REST",
                "credential_ref": "vault:prod/lake",
                "configuration": {"warehouse": "s3://warehouse"},
            },
        ),
    )
    created: dict[str, object] = _object(
        _send(
            app,
            "POST",
            "/api/v1/tables",
            {
                "data_source_id": _text(source, "id"),
                "name": table,
                "table_type": "TABLE",
                "physical_catalog": "catalog",
                "physical_namespace": "sales",
                "physical_table": table,
                "source_ref": {"catalog": "catalog", "namespace": "sales", "table": table},
                "source_description": "imported text",
                "table_format": "ICEBERG",
            },
        ),
    )
    return _text(created, "id")


def _airflow(request: httpx.Request) -> httpx.Response:
    path: str = request.url.path
    if path == "/auth/token":
        return httpx.Response(200, json={"access_token": _jwt()})
    if path == "/api/v2/version":
        return httpx.Response(200, json={"version": "3.3.2"})
    if path == "/api/v2/monitor/health":
        return httpx.Response(
            200,
            json={
                "metadatabase": {"status": "healthy"},
                "scheduler": {"status": "healthy"},
            },
        )
    if path == "/api/v2/dags":
        if request.url.params.get("offset", "0") not in {"0", ""}:
            return httpx.Response(200, json={"dags": [], "total_entries": 1})
        return httpx.Response(
            200,
            json={
                "dags": [
                    {
                        "dag_id": _DAG_ID,
                        "description": "nightly append",
                        "owners": ["admin"],
                        "tags": [{"name": "sales"}],
                        "is_paused": False,
                    }
                ],
                "total_entries": 1,
            },
        )
    if path == "/api/v2/assets":
        return httpx.Response(
            200,
            json={
                "assets": [
                    {
                        "uri": "iceberg://catalog/sales/orders",
                        "producing_tasks": [{"dag_id": _DAG_ID, "task_id": "write"}],
                    }
                ],
                "total_entries": 1,
            },
        )
    if path == "/api/v2/dagStats":
        return httpx.Response(
            200,
            json={"dags": [{"dag_id": _DAG_ID, "stats": [{"state": "success", "count": 1}]}]},
        )
    if path == f"/api/v2/dags/{_DAG_ID}/tasks":
        return httpx.Response(
            200,
            json={"tasks": [{"task_id": "write", "operator_name": "BashOperator"}]},
        )
    if path == f"/api/v2/dags/{_DAG_ID}/dagRuns":
        run: dict[str, str] = {
            "dag_run_id": "manual__1",
            "state": "success",
            "run_type": "manual",
        }
        return httpx.Response(200, json={"dag_runs": [run]})
    if path == f"/api/v2/dagSources/{_DAG_ID}":
        return httpx.Response(
            200,
            json={
                "dag_id": _DAG_ID,
                "content": "target = 'sales.customers'\n",
                "version_number": 3,
            },
        )
    return httpx.Response(404, json={"detail": "missing"})


def _app(
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[FastAPI, CatalogDatabase, httpx.Client]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    http: httpx.Client = httpx.Client(transport=httpx.MockTransport(handler))
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.http_client.override(providers.Object(http))
    return create_app(container), database, http


def _send(
    app: FastAPI,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> httpx.Response:
    return asyncio.run(_request(app, method, path, body))


def _object(response: httpx.Response) -> dict[str, object]:
    assert response.status_code < 400, response.text
    payload: object = response.json()
    assert isinstance(payload, dict)
    return payload


def _object_field(body: dict[str, object], key: str) -> dict[str, object]:
    value: object = body[key]
    assert isinstance(value, dict)
    return value


def _objects(body: dict[str, object], key: str) -> list[dict[str, object]]:
    value: object = body[key]
    assert isinstance(value, list)
    found: list[dict[str, object]] = []
    item: object
    for item in value:
        assert isinstance(item, dict)
        found.append(item)
    return found


def _text(body: dict[str, object], key: str) -> str:
    value: object = body[key]
    assert isinstance(value, str)
    return value


def _jwt() -> str:
    exp: int = int(time.time()) + 3600
    segment: str = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"e30.{segment}.sig"


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
