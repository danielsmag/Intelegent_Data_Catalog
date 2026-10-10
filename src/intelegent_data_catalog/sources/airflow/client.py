"""Airflow 3 REST client. Tokens come from POST /auth/token and are not logged."""

import base64
import json
import logging
import time
from collections.abc import Mapping
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from intelegent_data_catalog.exceptions import AirflowAuthError, AirflowUnavailableError
from intelegent_data_catalog.sources.airflow.models import (
    AirflowAsset,
    AirflowDag,
    AirflowDagRun,
    AirflowDagSource,
    AirflowDagStats,
    AirflowHealth,
    AirflowImportError,
    AirflowPlugin,
    AirflowPool,
    AirflowProvider,
    AirflowTask,
    AirflowVersion,
)
from intelegent_data_catalog.sources.trino.client import sanitize_engine_message

_PAGE_CAP: int = 5000
_TOKEN_SKEW_SECONDS: float = 30.0


class _AccessToken(BaseModel):
    """POST /auth/token body. The token itself is not stored on API responses."""

    model_config = ConfigDict(extra="ignore")

    access_token: str


class AirflowClient:
    """Calls one Airflow 3 API. A 401 refreshes the JWT once."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float,
        http_client: httpx.Client,
        logger: logging.Logger,
        page_size: int = 100,
    ) -> None:
        self._base_url: str = base_url.rstrip("/")
        self._username: str = username
        self._password: str = password
        self._timeout_seconds: float = timeout_seconds
        self._http: httpx.Client = http_client
        self._logger: logging.Logger = logger
        self._page_size: int = page_size
        self._token: str | None = None
        self._token_exp: float = 0.0

    def version(self) -> AirflowVersion:
        """Read the public version endpoint."""
        return _parse(AirflowVersion, self._public_get("/api/v2/version"))

    def health(self) -> AirflowHealth:
        """Read the public monitor health endpoint."""
        return _parse(AirflowHealth, self._public_get("/api/v2/monitor/health"))

    def authenticate(self) -> None:
        """Confirm the username and password can read DAGs."""
        self._get("/api/v2/dags", {"limit": 1, "offset": 0})

    def list_dags(self) -> list[AirflowDag]:
        """Return every DAG, following offset pages."""
        return self._paginate("/api/v2/dags", "dags", AirflowDag)

    def dag_tasks(self, dag_id: str) -> list[AirflowTask]:
        """Return tasks for one DAG."""
        payload: dict[str, object] = self._get(f"/api/v2/dags/{_dag_path(dag_id)}/tasks")
        return _parse_list(payload, "tasks", AirflowTask)

    def dag_runs(self, dag_id: str, limit: int) -> list[AirflowDagRun]:
        """Return the newest DAG runs."""
        payload: dict[str, object] = self._get(
            f"/api/v2/dags/{_dag_path(dag_id)}/dagRuns",
            {"limit": limit, "order_by": "-run_after"},
        )
        return _parse_list(payload, "dag_runs", AirflowDagRun)

    def dag_source(self, dag_id: str) -> AirflowDagSource:
        """Return the parsed source for one DAG."""
        return _parse(AirflowDagSource, self._get(f"/api/v2/dagSources/{_dag_path(dag_id)}"))

    def dag_stats(self, dag_ids: list[str]) -> list[AirflowDagStats]:
        """Return run counts for the given DAG ids."""
        if len(dag_ids) == 0:
            return []
        found: list[AirflowDagStats] = []
        start: int
        for start in range(0, len(dag_ids), 50):
            batch: list[str] = dag_ids[start : start + 50]
            params: list[tuple[str, str]] = [("dag_ids", dag_id) for dag_id in batch]
            payload: dict[str, object] = self._get("/api/v2/dagStats", params)
            found.extend(_parse_list(payload, "dags", AirflowDagStats))
        return found

    def assets(self) -> list[AirflowAsset]:
        """Return registered assets."""
        return self._paginate("/api/v2/assets", "assets", AirflowAsset)

    def pools(self) -> list[AirflowPool]:
        """Return pools."""
        return self._paginate("/api/v2/pools", "pools", AirflowPool)

    def providers(self) -> list[AirflowProvider]:
        """Return installed providers."""
        payload: dict[str, object] = self._get("/api/v2/providers")
        return _parse_list(payload, "providers", AirflowProvider)

    def plugins(self) -> list[AirflowPlugin]:
        """Return loaded plugins."""
        payload: dict[str, object] = self._get("/api/v2/plugins")
        return _parse_list(payload, "plugins", AirflowPlugin)

    def import_errors(self) -> list[AirflowImportError]:
        """Return DAG import errors."""
        return self._paginate("/api/v2/importErrors", "import_errors", AirflowImportError)

    def _paginate[ModelT: BaseModel](
        self,
        path: str,
        key: str,
        model: type[ModelT],
    ) -> list[ModelT]:
        found: list[ModelT] = []
        offset: int = 0
        while offset <= _PAGE_CAP:
            payload: dict[str, object] = self._get(
                path,
                {"limit": self._page_size, "offset": offset},
            )
            page: list[ModelT] = _parse_list(payload, key, model)
            found.extend(page)
            total: object = payload.get("total_entries")
            offset += len(page)
            if len(page) == 0:
                break
            if isinstance(total, int) and offset >= total:
                break
            if len(page) < self._page_size:
                break
        if offset > _PAGE_CAP:
            raise AirflowUnavailableError("Airflow returned too many pages")
        return found

    def _public_get(self, path: str) -> dict[str, object]:
        return self._send("GET", path, None, authenticated=False, retried=False)

    def _get(
        self,
        path: str,
        params: Mapping[str, str | int] | list[tuple[str, str]] | None = None,
    ) -> dict[str, object]:
        return self._send("GET", path, params, authenticated=True, retried=False)

    def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | list[tuple[str, str]] | None,
        *,
        authenticated: bool,
        retried: bool,
    ) -> dict[str, object]:
        headers: dict[str, str] = {}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._access_token(force=False)}"
        query: httpx.QueryParams | None = _query_params(params)
        try:
            response: httpx.Response = self._http.request(
                method,
                f"{self._base_url}{path}",
                params=query,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as err:
            raise AirflowUnavailableError(sanitize_engine_message(str(err))) from err
        if response.status_code == 401 and authenticated and not retried:
            self._logger.info("airflow token rejected for %s; refreshing", self._base_url)
            self._token = None
            return self._send(method, path, params, authenticated=True, retried=True)
        if response.status_code in {401, 403}:
            raise AirflowAuthError("Airflow rejected the credentials")
        if response.status_code >= 400:
            raise AirflowUnavailableError(f"Airflow returned {response.status_code}")
        return _json_object(response)

    def _access_token(self, *, force: bool) -> str:
        now: float = time.time()
        if not force and self._token is not None and now < self._token_exp - _TOKEN_SKEW_SECONDS:
            return self._token
        try:
            response: httpx.Response = self._http.post(
                f"{self._base_url}/auth/token",
                json={"username": self._username, "password": self._password},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as err:
            raise AirflowUnavailableError(sanitize_engine_message(str(err))) from err
        if response.status_code in {401, 403}:
            raise AirflowAuthError("Airflow rejected the credentials")
        if response.status_code >= 400:
            raise AirflowUnavailableError(f"Airflow returned {response.status_code}")
        token: _AccessToken = _parse(_AccessToken, _json_object(response))
        self._token = token.access_token
        self._token_exp = _jwt_expiry(token.access_token)
        return token.access_token


def _query_params(
    params: Mapping[str, str | int] | list[tuple[str, str]] | None,
) -> httpx.QueryParams | None:
    if params is None:
        return None
    query: httpx.QueryParams = httpx.QueryParams()
    if isinstance(params, list):
        pair: tuple[str, str]
        for pair in params:
            query = query.add(pair[0], pair[1])
        return query
    key: str
    value: str | int
    for key, value in params.items():
        query = query.add(key, value)
    return query


def _dag_path(dag_id: str) -> str:
    return quote(dag_id, safe="")


def _json_object(response: httpx.Response) -> dict[str, object]:
    try:
        payload: object = response.json()
    except ValueError as err:
        raise AirflowUnavailableError("Airflow returned an unexpected payload") from err
    if not isinstance(payload, dict):
        raise AirflowUnavailableError("Airflow returned an unexpected payload")
    return payload


def _parse[ModelT: BaseModel](model: type[ModelT], payload: dict[str, object]) -> ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as err:
        raise AirflowUnavailableError("Airflow returned an unexpected payload") from err


def _parse_list[ModelT: BaseModel](
    payload: dict[str, object],
    key: str,
    model: type[ModelT],
) -> list[ModelT]:
    raw: object = payload.get(key)
    if not isinstance(raw, list):
        raise AirflowUnavailableError("Airflow returned an unexpected payload")
    found: list[ModelT] = []
    item: object
    for item in raw:
        if not isinstance(item, dict):
            raise AirflowUnavailableError("Airflow returned an unexpected payload")
        found.append(_parse(model, item))
    return found


def _jwt_expiry(token: str) -> float:
    parts: list[str] = token.split(".")
    if len(parts) < 2:
        return time.time() + 300
    segment: str = parts[1]
    padding: str = "=" * (-len(segment) % 4)
    try:
        decoded: bytes = base64.urlsafe_b64decode(segment + padding)
        payload: object = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return time.time() + 300
    if not isinstance(payload, dict):
        return time.time() + 300
    exp: object = payload.get("exp")
    if isinstance(exp, int | float) and not isinstance(exp, bool):
        return float(exp)
    return time.time() + 300
