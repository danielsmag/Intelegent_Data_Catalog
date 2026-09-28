"""SQL guard and query-service tests. Spark is not started."""

from intelegent_data_catalog.catalog.models import QueryColumn, QueryResult
from intelegent_data_catalog.catalog.query_service import QueryService
from intelegent_data_catalog.catalog.sql_guard import SqlGuard
from intelegent_data_catalog.core.settings import QuerySettings
from intelegent_data_catalog.exceptions import QueryRejectedError


class _RecordingSpark:
    """Captures SQL and returns a fixed frame."""

    def __init__(self, rows: list[list[object]]) -> None:
        self.statements: list[str] = []
        self._rows: list[list[object]] = rows

    def execute(self, sql: str) -> QueryResult:
        """Record the statement and return the canned rows."""
        self.statements.append(sql)
        columns: list[QueryColumn] = [QueryColumn(name="customer_id", type="long")]
        return QueryResult(columns=columns, rows=self._rows, truncated=False)


def test_prepare_wraps_a_select_with_the_row_cap() -> None:
    """A qualified select is rewritten with LIMIT max_rows + 1."""
    guard: SqlGuard = _guard()
    prepared: str = guard.prepare("SELECT * FROM catalog.sales.customers")
    assert "catalog.sales.customers" in prepared
    assert prepared.endswith("LIMIT 3")


def test_prepare_allows_namespace_table_and_a_join() -> None:
    """Two-part names and joins stay read-only queries."""
    guard: SqlGuard = _guard()
    prepared: str = guard.prepare(
        "SELECT c.name FROM sales.customers AS c "
        "JOIN sales.orders AS o ON c.customer_id = o.customer_id"
    )
    assert "sales.customers" in prepared
    assert "sales.orders" in prepared


def test_prepare_allows_a_cte() -> None:
    """A query can refer to its own CTE by a single name."""
    guard: SqlGuard = _guard()
    prepared: str = guard.prepare(
        "WITH spend AS (SELECT * FROM sales.customer_spend) SELECT * FROM spend"
    )
    assert "customer_spend" in prepared
    assert "spend" in prepared


def test_prepare_strips_a_trailing_semicolon() -> None:
    """One statement may end with a semicolon."""
    guard: SqlGuard = _guard()
    prepared: str = guard.prepare("SELECT * FROM sales.customers;")
    assert "sales.customers" in prepared


def test_prepare_rejects_a_second_statement() -> None:
    """A select followed by a drop is not one query."""
    guard: SqlGuard = _guard()
    try:
        guard.prepare("SELECT * FROM sales.customers; DROP TABLE sales.customers")
    except QueryRejectedError as err:
        assert "one statement" in str(err)
        return
    raise AssertionError("expected QueryRejectedError")


def test_prepare_rejects_writes_and_table_functions() -> None:
    """Inserts, drops, and table functions never reach Spark."""
    guard: SqlGuard = _guard()
    rejected: list[str] = [
        "INSERT INTO sales.customers VALUES (1, 'a', 'a', 'a')",
        "DROP TABLE sales.customers",
        "UPDATE sales.customers SET name = 'x'",
        "DELETE FROM sales.customers",
        "SELECT * FROM range(3)",
        "SELECT * FROM explode(array(1))",
        "SET spark.sql.shuffle.partitions = 1",
        "SELECT * FROM customers",
        "SELECT * FROM other.sales.customers",
    ]
    statement: str
    for statement in rejected:
        try:
            guard.prepare(statement)
        except QueryRejectedError:
            continue
        raise AssertionError(f"expected rejection for {statement}")


def test_prepare_rejects_empty_and_oversized_sql() -> None:
    """Empty text and text past the character cap are rejected."""
    guard: SqlGuard = _guard()
    try:
        guard.prepare("   ")
    except QueryRejectedError as err:
        assert str(err) == "SQL is empty"
    else:
        raise AssertionError("expected empty SQL to be rejected")
    try:
        guard.prepare("SELECT * FROM sales.customers /* " + ("x" * 400) + " */")
    except QueryRejectedError as err:
        assert "characters" in str(err)
    else:
        raise AssertionError("expected oversized SQL to be rejected")


def test_query_service_marks_results_past_the_row_cap_truncated() -> None:
    """The extra cap row is dropped and truncated is set."""
    spark: _RecordingSpark = _RecordingSpark(rows=[[1], [2], [3]])
    settings: QuerySettings = QuerySettings(max_query_rows=2, max_sql_chars=200)
    service: QueryService = QueryService(spark=spark, guard=_guard(), settings=settings)
    result: QueryResult = service.query("SELECT * FROM sales.customers")
    assert result.truncated is True
    assert result.rows == [[1], [2]]
    assert len(spark.statements) == 1


def test_sample_uses_the_requested_limit() -> None:
    """Sample SQL names the table and keeps the caller limit."""
    spark: _RecordingSpark = _RecordingSpark(rows=[[1]])
    settings: QuerySettings = QuerySettings(
        sample_limit=100,
        max_sample_limit=500,
        max_query_rows=10,
    )
    service: QueryService = QueryService(spark=spark, guard=_guard(), settings=settings)
    result: QueryResult = service.sample("catalog", "sales", "customers", 5)
    assert result.truncated is False
    assert "LIMIT 5" in spark.statements[0]


def test_sample_rejects_a_limit_above_the_maximum() -> None:
    """A sample limit past the configured maximum is a 400 at the service."""
    spark: _RecordingSpark = _RecordingSpark(rows=[])
    settings: QuerySettings = QuerySettings(sample_limit=2, max_sample_limit=5, max_query_rows=10)
    service: QueryService = QueryService(spark=spark, guard=_guard(), settings=settings)
    try:
        service.sample("catalog", "sales", "customers", 6)
    except QueryRejectedError as err:
        assert "between 1 and 5" in str(err)
    else:
        raise AssertionError("expected the sample limit to be rejected")
    assert spark.statements == []


def _guard() -> SqlGuard:
    return SqlGuard(default_catalog="catalog", max_rows=2, max_sql_chars=400)
