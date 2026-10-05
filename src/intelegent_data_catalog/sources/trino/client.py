"""Bounded Trino client. Errors never include credential text."""

import re
from collections.abc import Sequence
from typing import Protocol

from intelegent_data_catalog.exceptions import TrinoUnavailableError

_SECRET_TEXT: re.Pattern[str] = re.compile(
    r"(?i)(password|secret|token|credential)\s*[:=]\s*\S+",
)


class TrinoQuery(Protocol):
    """Runs one read-only metadata statement."""

    def query(self, sql: str) -> list[dict[str, object]]:
        """Return rows. Column names are lowercased."""
        ...


def _materialize_rows(
    description: Sequence[object] | None,
    raw_rows: Sequence[object],
) -> list[dict[str, object]]:
    """Map driver rows to dicts. The Trino driver returns lists, not tuples."""
    names: list[str] = []
    if description is not None:
        column: object
        for column in description:
            name: object = getattr(column, "name", None)
            if not isinstance(name, str):
                name = column[0] if isinstance(column, tuple) and len(column) > 0 else "column"
            names.append(str(name).lower())
    rows: list[dict[str, object]] = []
    raw: object
    for raw in raw_rows:
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            continue
        record: dict[str, object] = {}
        index: int
        value: object
        for index, value in enumerate(raw):
            key: str = names[index] if index < len(names) else f"column_{index}"
            record[key] = value
        rows.append(record)
    return rows


def sanitize_engine_message(message: str) -> str:
    """Drop credential-shaped fragments from an engine error."""
    redacted: str = _SECRET_TEXT.sub(r"\1=***", message)
    collapsed: str = " ".join(redacted.split())
    if len(collapsed) > 500:
        return collapsed[:500]
    return collapsed


class TrinoClient:
    """Official Trino DB-API client with a timeout and sanitized failures."""

    def __init__(self, host: str, port: int, user: str, timeout_seconds: float) -> None:
        self._host: str = host
        self._port: int = port
        self._user: str = user
        self._timeout_seconds: float = timeout_seconds

    def query(self, sql: str) -> list[dict[str, object]]:
        """Execute one metadata statement and fetch the full result."""
        try:
            import trino
            from trino.exceptions import TrinoQueryError
        except ImportError as err:
            raise TrinoUnavailableError("Trino client is not installed") from err
        try:
            connection = trino.dbapi.connect(  # type: ignore[no-untyped-call]
                host=self._host,
                port=self._port,
                user=self._user,
                http_scheme="http",
                request_timeout=self._timeout_seconds,
            )
        except Exception as err:
            raise TrinoUnavailableError(sanitize_engine_message(str(err))) from err
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(sql)
                description: Sequence[object] | None = cursor.description
                raw_rows: list[object] = list(cursor.fetchall())
            except TrinoQueryError as err:
                raise TrinoUnavailableError(sanitize_engine_message(str(err))) from err
            finally:
                cursor.close()
        finally:
            connection.close()
        return _materialize_rows(description, raw_rows)
