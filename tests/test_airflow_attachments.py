"""Attach an Airflow instance or DAG to a space or scope."""

from pathlib import Path

import httpx
from fastapi import FastAPI

from intelegent_data_catalog.persistence.database import CatalogDatabase
from tests.test_airflow_api import _airflow, _app, _instance, _object, _send, _text


def test_attach_instance_is_dynamic_and_rejects_duplicates(tmp_path: Path) -> None:
    """A later sync joins an attached instance. A second attach of the same pair is 409."""
    app: FastAPI
    database: CatalogDatabase
    http: httpx.Client
    app, database, http = _app(tmp_path, _airflow)
    try:
        scope_id: str = _scope(app)
        created_instance: dict[str, object] = _object(
            _send(app, "POST", "/api/v1/airflow/instances", _instance("Local")),
        )
        instance_id: str = _text(created_instance, "id")
        attached: dict[str, object] = _object(
            _send(
                app,
                "POST",
                f"/api/v1/scopes/{scope_id}/airflow",
                {"instance_id": instance_id},
            )
        )
        assert attached["dag_id"] is None
        before: list[object] = _send(app, "GET", f"/api/v1/scopes/{scope_id}/airflow/dags").json()
        assert before == []
        synced: dict[str, object] = _object(
            _send(app, "POST", f"/api/v1/airflow/instances/{instance_id}/sync", {}),
        )
        assert synced["status"] in {"SUCCEEDED", "PARTIAL"}
        dag_response: httpx.Response = _send(app, "GET", f"/api/v1/scopes/{scope_id}/airflow/dags")
        dags: list[dict[str, object]] = dag_response.json()
        assert len(dags) == 1
        assert dags[0]["dag_id"] == "append_sales_customer"
        assert dags[0]["via"] == "instance"
        duplicate = _send(
            app,
            "POST",
            f"/api/v1/scopes/{scope_id}/airflow",
            {"instance_id": instance_id},
        )
        assert duplicate.status_code == 409
        missing = _send(
            app,
            "POST",
            "/api/v1/scopes/00000000-0000-0000-0000-000000000099/airflow",
            {"instance_id": instance_id},
        )
        assert missing.status_code == 404
        attachment_id: str = _text(attached, "id")
        removed = _send(app, "DELETE", f"/api/v1/scopes/{scope_id}/airflow/{attachment_id}")
        assert removed.status_code == 204
        left: list[object] = _send(app, "GET", f"/api/v1/scopes/{scope_id}/airflow").json()
        assert left == []
    finally:
        http.close()
        database.dispose()


def test_dag_attachment_reaches_a_space_and_drops_with_the_instance(tmp_path: Path) -> None:
    """A DAG attached to a scope is visible from its space. Deleting the instance removes it."""
    app: FastAPI
    database: CatalogDatabase
    http: httpx.Client
    app, database, http = _app(tmp_path, _airflow)
    try:
        space_id: str
        scope_id: str
        space_id, scope_id = _space(app)
        instance_id: str = _text(
            _object(_send(app, "POST", "/api/v1/airflow/instances", _instance("Local"))),
            "id",
        )
        _send(app, "POST", f"/api/v1/airflow/instances/{instance_id}/sync", {})
        attached: dict[str, object] = _object(
            _send(
                app,
                "POST",
                f"/api/v1/scopes/{scope_id}/airflow",
                {"instance_id": instance_id, "dag_id": "append_sales_customer"},
            )
        )
        assert attached["dag_id"] == "append_sales_customer"
        visible_response: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/spaces/{space_id}/airflow/dags",
        )
        visible: list[dict[str, object]] = visible_response.json()
        assert len(visible) == 1
        assert visible[0]["via"] == "dag"
        deleted = _send(app, "DELETE", f"/api/v1/airflow/instances/{instance_id}")
        assert deleted.status_code == 204
        left: list[object] = _send(app, "GET", f"/api/v1/scopes/{scope_id}/airflow").json()
        assert left == []
        gone: list[object] = _send(app, "GET", f"/api/v1/spaces/{space_id}/airflow/dags").json()
        assert gone == []
    finally:
        http.close()
        database.dispose()


def _scope(app: FastAPI) -> str:
    space_id: str
    scope_id: str
    space_id, scope_id = _space(app)
    del space_id
    return scope_id


def _space(app: FastAPI) -> tuple[str, str]:
    space: dict[str, object] = _object(_send(app, "POST", "/api/v1/spaces", {"name": "Commerce"}))
    space_id: str = _text(space, "id")
    scope: dict[str, object] = _object(
        _send(app, "POST", f"/api/v1/spaces/{space_id}/scopes", {"name": "Ordering"}),
    )
    return space_id, _text(scope, "id")
