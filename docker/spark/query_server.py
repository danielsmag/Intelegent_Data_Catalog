"""HTTP front for one long-lived Spark session."""

import base64
import json
import math
import os
import threading
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from pyspark.errors import AnalysisException, ParseException
from pyspark.sql import DataFrame, Row, SparkSession
from pyspark.sql.types import StructField

_MAX_BODY_BYTES: int = 1_000_000


class SparkQueryServer(ThreadingHTTPServer):
    """Spark session shared by query requests."""

    def __init__(self, server_address: tuple[str, int], spark: SparkSession) -> None:
        super().__init__(server_address, QueryHandler)
        self.spark: SparkSession = spark
        self.sql_lock: threading.Lock = threading.Lock()

    def rebuild_spark(self) -> None:
        """Start a new session so Iceberg requests a new Polaris token."""
        self.spark.stop()
        self.spark = SparkSession.builder.getOrCreate()


class QueryHandler(BaseHTTPRequestHandler):
    """GET /health and POST /sql."""

    def do_GET(self) -> None:
        """Report that the Spark session is accepting queries."""
        if self.path.split("?", 1)[0] != "/health":
            self._reply(404, {"detail": "not found"})
            return
        self._reply(200, {"status": "ok"})

    def do_POST(self) -> None:
        """Run one SQL statement and return columns plus rows."""
        if self.path.split("?", 1)[0] != "/sql":
            self._reply(404, {"detail": "not found"})
            return
        raw_sql: str | None = self._read_sql()
        if raw_sql is None:
            return
        server: SparkQueryServer = cast(SparkQueryServer, self.server)
        try:
            with server.sql_lock:
                try:
                    materialized, fields = _collect(server.spark, raw_sql)
                except Exception as err:
                    if not _is_not_authorized(err):
                        raise
                    print("polaris rejected the token; restarting the spark session")
                    server.rebuild_spark()
                    materialized, fields = _collect(server.spark, raw_sql)
        except (AnalysisException, ParseException) as err:
            status: int = 404 if _is_missing_table(str(err)) else 400
            self._reply(status, {"detail": _short(str(err))})
            return
        except Exception as err:
            self._reply(500, {"detail": _short(str(err))})
            return
        columns: list[dict[str, str]] = [
            {"name": field.name, "type": field.dataType.simpleString()} for field in fields
        ]
        rows: list[list[object]] = []
        row: Row
        for row in materialized:
            rows.append([_cell(row[index]) for index in range(len(fields))])
        self._reply(200, {"columns": columns, "rows": rows})

    def log_message(self, format: str, *args: object) -> None:
        """Keep request logs on stdout for Compose."""
        message: str = format % args
        print(f"{self.address_string()} {message}")

    def _read_sql(self) -> str | None:
        length_header: str | None = self.headers.get("Content-Length")
        if length_header is None or not length_header.isdigit():
            self._reply(400, {"detail": "content length is required"})
            return None
        length: int = int(length_header)
        if length > _MAX_BODY_BYTES:
            self._reply(400, {"detail": "request body is too large"})
            return None
        raw: bytes = self.rfile.read(length)
        try:
            payload: object = json.loads(raw)
        except json.JSONDecodeError:
            self._reply(400, {"detail": "invalid JSON"})
            return None
        if not isinstance(payload, dict):
            self._reply(400, {"detail": "sql must be a string"})
            return None
        sql: object = payload.get("sql")
        if not isinstance(sql, str) or sql.strip() == "":
            self._reply(400, {"detail": "sql must be a string"})
            return None
        return sql

    def _reply(self, status: int, body: dict[str, object]) -> None:
        encoded: bytes = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    """Build the Spark session, then serve queries."""
    spark: SparkSession = SparkSession.builder.getOrCreate()
    port: int = int(os.environ.get("QUERY_PORT", "8091"))
    server: SparkQueryServer = SparkQueryServer(("0.0.0.0", port), spark)
    print(f"spark query server listening on {port}")
    try:
        server.serve_forever()
    finally:
        server.spark.stop()


def _collect(
    spark: SparkSession,
    sql: str,
) -> tuple[list[Row], list[StructField]]:
    frame: DataFrame = spark.sql(sql)
    materialized: list[Row] = frame.collect()
    fields: list[StructField] = list(frame.schema.fields)
    return materialized, fields


def _is_not_authorized(err: Exception) -> bool:
    return "NotAuthorizedException" in str(err)


def _is_missing_table(message: str) -> bool:
    folded: str = message.upper()
    if "TABLE_OR_VIEW_NOT_FOUND" in folded:
        return True
    return "TABLE OR VIEW NOT FOUND" in folded


def _short(message: str) -> str:
    text: str = message.strip()
    if len(text) > 500:
        return text[:500]
    return text


def _cell(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Row):
        mapped: dict[str, object] = value.asDict(recursive=True)
        return _cell(mapped)
    if isinstance(value, dict):
        rendered: dict[str, object] = {}
        key: object
        item: object
        for key, item in value.items():
            rendered[str(key)] = _cell(item)
        return rendered
    if isinstance(value, list | tuple):
        return [_cell(item) for item in value]
    return str(value)


if __name__ == "__main__":
    main()
