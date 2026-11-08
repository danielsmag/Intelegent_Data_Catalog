"""Run one table's profile statement and parse the aggregate row."""

from datetime import datetime
from typing import Protocol

from pydantic import JsonValue

from intelegent_data_catalog.assets.definitions.models import IcebergTableSourceRef
from intelegent_data_catalog.assets.profiling.models import ColumnProfile, TableProfile
from intelegent_data_catalog.assets.profiling.schema import primitive_columns
from intelegent_data_catalog.assets.profiling.sql import ProfileSql
from intelegent_data_catalog.catalog.models import QueryColumn, QueryResult
from intelegent_data_catalog.persistence.clock import utc_now


class TableMetadataReader(Protocol):
    """Iceberg load used while profiling one table."""

    def load_table_raw(self, catalog: str, namespace: str, table: str) -> dict[str, JsonValue]:
        """Return the full load-table document."""


class ProfileQuery(Protocol):
    """Read-only SQL used for one profile statement."""

    def query(self, sql: str) -> QueryResult:
        """Run one read-only statement."""


class TableProfileResult:
    """One table profile and whether the column list was capped."""

    def __init__(self, table: TableProfile, columns_truncated: bool) -> None:
        self.table: TableProfile = table
        self.columns_truncated: bool = columns_truncated


class IcebergTableProfiler:
    """Profiles one Iceberg table through Polaris metadata and read-only SQL."""

    def __init__(self, iceberg: TableMetadataReader, query: ProfileQuery) -> None:
        self._iceberg: TableMetadataReader = iceberg
        self._query: ProfileQuery = query
        self._sql: ProfileSql = ProfileSql()

    def profile(self, ref: IcebergTableSourceRef, max_columns: int) -> TableProfileResult:
        """Load the schema, aggregate primitive columns, and return typed stats."""
        raw: dict[str, JsonValue] = self._iceberg.load_table_raw(
            ref.catalog,
            ref.namespace,
            ref.table,
        )
        fields: list[tuple[str, str]] = primitive_columns(raw)
        shown: list[tuple[str, str]] = fields[:max_columns]
        sql: str = self._sql.statement(ref.catalog, ref.namespace, ref.table, shown)
        result: QueryResult = self._query.query(sql)
        profiled_at: datetime = utc_now()
        reader: _StatRow = _StatRow(result)
        columns: list[ColumnProfile] = [
            _column_profile(name, iceberg_type, reader) for name, iceberg_type in shown
        ]
        row_count: int | None = reader.integer("row_count")
        table: TableProfile = TableProfile(
            catalog=ref.catalog,
            namespace=ref.namespace,
            table=ref.table,
            row_count=row_count,
            columns=columns,
            columns_truncated=len(fields) > len(shown),
            sql=sql,
            profiled_at=profiled_at,
        )
        return TableProfileResult(table=table, columns_truncated=table.columns_truncated)


class _StatRow:
    """One aggregate row addressed by result column name."""

    def __init__(self, result: QueryResult) -> None:
        self._values: dict[str, JsonValue] = {}
        if len(result.rows) == 0:
            return
        row: list[JsonValue] = result.rows[0]
        column: QueryColumn
        index: int
        for index, column in enumerate(result.columns):
            self._values[column.name] = row[index] if index < len(row) else None

    def integer(self, name: str) -> int | None:
        """Return a non-negative integer, rounding floats from approximate counts."""
        value: JsonValue | None = self._values.get(name)
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float):
            rounded: int = round(value)
            return rounded if value >= 0 else None
        if isinstance(value, str):
            return _integer_text(value)
        return None

    def number(self, name: str) -> float | None:
        """Return a float statistic."""
        value: JsonValue | None = self._values.get(name)
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return _float_text(value)
        return None

    def text(self, name: str) -> str | None:
        """Return a min or max rendered by Spark."""
        value: JsonValue | None = self._values.get(name)
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return None


def _column_profile(name: str, iceberg_type: str, row: _StatRow) -> ColumnProfile:
    null_count: int | None = row.integer(f"{name}__nulls")
    distinct_count: int | None = row.integer(f"{name}__distinct")
    row_count: int | None = row.integer("row_count")
    candidate: bool = (
        row_count is not None
        and row_count > 0
        and null_count == 0
        and distinct_count == row_count
    )
    max_length: int | None = row.integer(f"{name}__max_len")
    return ColumnProfile(
        name=name,
        iceberg_type=iceberg_type,
        null_count=null_count,
        null_ratio=_ratio(null_count, row_count),
        distinct_count=distinct_count,
        uniqueness_ratio=_ratio(distinct_count, row_count),
        minimum=row.text(f"{name}__min"),
        maximum=row.text(f"{name}__max"),
        mean=row.number(f"{name}__mean"),
        stddev=row.number(f"{name}__stddev"),
        avg_length=row.number(f"{name}__avg_len"),
        max_length=max_length,
        true_count=row.integer(f"{name}__true"),
        candidate_key=candidate,
    )


def _ratio(part: int | None, whole: int | None) -> float | None:
    if part is None or whole is None or whole <= 0:
        return None
    ratio: float = part / whole
    if ratio > 1:
        return 1.0
    if ratio < 0:
        return 0.0
    return ratio


def _integer_text(value: str) -> int | None:
    try:
        number: float = float(value)
    except ValueError:
        return None
    if number < 0:
        return None
    rounded: int = round(number)
    return rounded


def _float_text(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
