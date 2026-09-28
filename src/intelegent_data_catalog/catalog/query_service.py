"""Runs sample scans and read-only SQL through Spark."""

from intelegent_data_catalog.catalog.models import QueryResult
from intelegent_data_catalog.catalog.spark_query import SparkSqlClient
from intelegent_data_catalog.catalog.sql_guard import SqlGuard
from intelegent_data_catalog.core.settings import QuerySettings
from intelegent_data_catalog.exceptions import QueryRejectedError


class QueryService:
    """Prepares SQL, asks Spark to run it, and applies the row cap."""

    def __init__(self, spark: SparkSqlClient, guard: SqlGuard, settings: QuerySettings) -> None:
        self._spark: SparkSqlClient = spark
        self._guard: SqlGuard = guard
        self._settings: QuerySettings = settings

    def sample(self, catalog: str, namespace: str, table: str, limit: int | None) -> QueryResult:
        """Return a limited row preview of one table."""
        row_limit: int = self._sample_limit(limit)
        sql: str = f"SELECT * FROM {catalog}.{namespace}.{table} LIMIT {row_limit}"
        return self._run(sql)

    def query(self, sql: str) -> QueryResult:
        """Return rows for one read-only statement."""
        return self._run(sql)

    def _run(self, sql: str) -> QueryResult:
        prepared: str = self._guard.prepare(sql)
        result: QueryResult = self._spark.execute(prepared)
        return self._cap(result)

    def _sample_limit(self, limit: int | None) -> int:
        if limit is None:
            return self._settings.sample_limit
        if limit < 1 or limit > self._settings.max_sample_limit:
            raise QueryRejectedError(
                f"sample limit must be between 1 and {self._settings.max_sample_limit}"
            )
        return limit

    def _cap(self, result: QueryResult) -> QueryResult:
        cap: int = self._settings.max_query_rows
        if len(result.rows) <= cap:
            return result
        return QueryResult(columns=result.columns, rows=result.rows[:cap], truncated=True)
