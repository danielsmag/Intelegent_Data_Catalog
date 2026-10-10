"""Airflow 3 client: token cache, one 401 refresh, pagination, sanitized failures."""

import base64
import json
import logging
import time
from collections.abc import Callable

import httpx

from intelegent_data_catalog.exceptions import AirflowUnavailableError
from intelegent_data_catalog.sources.airflow.client import AirflowClient
from intelegent_data_catalog.sources.airflow.models import AirflowDag


def test_token_is_fetched_once_and_sent_as_bearer() -> None:
    """The first authenticated call posts /auth/token and reuses the JWT."""
    script: _Script = _Script()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/token":
            script.token_calls += 1
            body: object = json.loads(request.content)
            assert body == {"username": "admin", "password": "s3cret-airflow"}
            return _json({"access_token": _jwt()})
        script.authorizations.append(request.headers.get("Authorization", ""))
        return _json({"dags": [{"dag_id": "append_sales_customer"}], "total_entries": 1})

    client: AirflowClient = _client(handler)
    first: list[AirflowDag] = client.list_dags()
    second: list[AirflowDag] = client.list_dags()
    assert [dag.dag_id for dag in first] == ["append_sales_customer"]
    assert [dag.dag_id for dag in second] == ["append_sales_customer"]
    assert script.token_calls == 1
    assert script.authorizations == [f"Bearer {_jwt()}", f"Bearer {_jwt()}"]


def test_rejected_token_is_refreshed_once() -> None:
    """A 401 clears the cache and authenticates one more time."""
    script: _Script = _Script()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/token":
            script.token_calls += 1
            return _json({"access_token": _jwt()})
        script.dag_calls += 1
        if script.dag_calls == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return _json({"dags": [{"dag_id": "append_sales_customer"}], "total_entries": 1})

    found: list[AirflowDag] = _client(handler).list_dags()
    assert [dag.dag_id for dag in found] == ["append_sales_customer"]
    assert script.token_calls == 2
    assert script.dag_calls == 2


def test_dag_pages_follow_offset() -> None:
    """limit and offset walk total_entries."""
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/token":
            return _json({"access_token": _jwt()})
        offset: str = request.url.params.get("offset", "")
        offsets.append(offset)
        if offset == "0":
            page: list[dict[str, str]] = [{"dag_id": "one"}, {"dag_id": "two"}]
        else:
            page = [{"dag_id": "three"}]
        return _json({"dags": page, "total_entries": 3})

    found: list[AirflowDag] = _client(handler, page_size=2).list_dags()
    assert [dag.dag_id for dag in found] == ["one", "two", "three"]
    assert offsets == ["0", "2"]


def test_connection_failure_drops_secrets() -> None:
    """A transport error is sanitized before it leaves the client."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("password=hunter2 refused")

    try:
        _client(handler).version()
    except AirflowUnavailableError as err:
        message: str = str(err)
    else:
        raise AssertionError("expected AirflowUnavailableError")
    assert "hunter2" not in message
    assert "password=***" in message


class _Script:
    """Counts token and DAG calls for one mock."""

    def __init__(self) -> None:
        self.token_calls: int = 0
        self.dag_calls: int = 0
        self.authorizations: list[str] = []


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    page_size: int = 100,
) -> AirflowClient:
    transport: httpx.MockTransport = httpx.MockTransport(handler)
    return AirflowClient(
        base_url="http://airflow.test",
        username="admin",
        password="s3cret-airflow",
        timeout_seconds=5.0,
        http_client=httpx.Client(transport=transport),
        logger=logging.getLogger("test.airflow"),
        page_size=page_size,
    )


def _json(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _jwt() -> str:
    exp: int = int(time.time()) + 3600
    segment: str = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"e30.{segment}.sig"
