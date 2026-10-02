"""HTTP client for the long-lived Spark SQL service."""

import logging
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from intelegent_data_catalog.catalog.models import QueryColumn, QueryResult
from intelegent_data_catalog.exceptions import (
    QueryNotFoundError,
    QueryRejectedError,
    QueryTimeoutError,
    SparkQueryError,
)


class SparkSqlClient(Protocol):
    """Runs one prepared Spark SQL statement."""

    def execute(self, sql: str) -> QueryResult:
        """Return columns and rows for a statement the guard already accepted."""
        ...


class _SparkPayload(BaseModel):
    """JSON body from POST /sql."""

    model_config = ConfigDict(extra="ignore")

    columns: list[QueryColumn]
    rows: list[list[JsonValue]]


class _SparkErrorBody(BaseModel):
    """JSON error body from the Spark query server."""

    model_config = ConfigDict(extra="ignore")

    detail: str = ""


class SparkQueryClient:
    """Calls the Spark query server and maps its HTTP status."""

    def __init__(
        self,
        http_client: httpx.Client,
        spark_url: str,
        timeout_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self._http: httpx.Client = http_client
        self._sql_url: str = f"{spark_url.rstrip('/')}/sql"
        self._timeout: httpx.Timeout = httpx.Timeout(timeout_seconds)
        self._logger: logging.Logger = logger

    def execute(self, sql: str) -> QueryResult:
        """Run SQL and return rows. Truncation is applied by the query service."""
        try:
            response: httpx.Response = self._http.post(
                self._sql_url,
                json={"sql": sql},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as err:
            raise QueryTimeoutError("Spark query timed out") from err
        except httpx.HTTPError as err:
            raise SparkQueryError(f"Spark query service failed: {err}") from err
        self._raise_for_status(response)
        return self._parse_result(response)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        detail: str = _detail(response)
        if response.status_code == 404:
            raise QueryNotFoundError(detail)
        if response.status_code == 400:
            raise QueryRejectedError(detail)
        self._logger.warning("spark query failed status=%s detail=%s", response.status_code, detail)
        raise SparkQueryError(detail)

    def _parse_result(self, response: httpx.Response) -> QueryResult:
        try:
            payload: object = response.json()
        except ValueError as err:
            raise SparkQueryError("Spark query service returned invalid JSON") from err
        try:
            parsed: _SparkPayload = _SparkPayload.model_validate(payload)
        except ValidationError as err:
            raise SparkQueryError("Spark query service returned an unexpected payload") from err
        return QueryResult(columns=parsed.columns, rows=parsed.rows, truncated=False)


def _detail(response: httpx.Response) -> str:
    fallback: str = f"Spark query failed: HTTP {response.status_code}"
    try:
        payload: object = response.json()
    except ValueError:
        text: str = response.text.strip()
        if text == "":
            return fallback
        if len(text) > 300:
            return text[:300]
        return text
    try:
        body: _SparkErrorBody = _SparkErrorBody.model_validate(payload)
    except ValidationError:
        return fallback
    if body.detail == "":
        return fallback
    return body.detail
