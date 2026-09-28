"""Accepts one read-only Spark SQL statement and caps its result size."""

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from intelegent_data_catalog.exceptions import QueryRejectedError

_IDENT: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_QUERY_TYPES: tuple[type[exp.Expr], ...] = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
)
_FORBIDDEN: tuple[type[exp.Expr], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Command,
    exp.Set,
    exp.Copy,
    exp.Describe,
    exp.Use,
    exp.Attach,
    exp.Analyze,
    exp.Cache,
    exp.Uncache,
    exp.Transaction,
)


class SqlGuard:
    """Checks a statement, then wraps it with the row cap."""

    def __init__(self, default_catalog: str, max_rows: int, max_sql_chars: int) -> None:
        self._default_catalog: str = default_catalog
        self._max_rows: int = max_rows
        self._max_sql_chars: int = max_sql_chars

    def prepare(self, sql: str) -> str:
        """Return Spark SQL that runs the query and stops after the row cap."""
        text: str = sql.strip()
        if text == "":
            raise QueryRejectedError("SQL is empty")
        if len(text) > self._max_sql_chars:
            raise QueryRejectedError(f"SQL exceeds {self._max_sql_chars} characters")
        expression: exp.Expr = _one_statement(text)
        if not isinstance(expression, _QUERY_TYPES):
            raise QueryRejectedError("only a single read-only query is allowed")
        _reject_forbidden(expression)
        _check_tables(expression, self._default_catalog)
        inner: str = expression.sql(dialect="spark")
        row_cap: int = self._max_rows + 1
        return f"SELECT * FROM ({inner}) AS result_rows LIMIT {row_cap}"


def _one_statement(sql: str) -> exp.Expr:
    try:
        parsed: list[exp.Expr | None] = sqlglot.parse(sql, read="spark")
    except SqlglotError as err:
        raise QueryRejectedError("SQL could not be parsed") from err
    statements: list[exp.Expr] = [item for item in parsed if item is not None]
    if len(statements) != 1 or len(parsed) != 1:
        raise QueryRejectedError("SQL must be one statement")
    return statements[0]


def _reject_forbidden(expression: exp.Expr) -> None:
    node: exp.Expr
    for node in expression.walk():
        if isinstance(node, _FORBIDDEN):
            raise QueryRejectedError("only a single read-only query is allowed")


def _check_tables(expression: exp.Expr, default_catalog: str) -> None:
    cte_names: set[str] = set()
    cte: exp.CTE
    for cte in expression.find_all(exp.CTE):
        alias: str = cte.alias
        if alias != "":
            cte_names.add(alias.casefold())
    table: exp.Table
    for table in expression.find_all(exp.Table):
        _check_table(table, cte_names, default_catalog)


def _check_table(table: exp.Table, cte_names: set[str], default_catalog: str) -> None:
    if not isinstance(table.this, exp.Identifier):
        raise QueryRejectedError("table functions are not allowed")
    name: str = table.name
    namespace: str = table.db
    catalog: str = table.catalog
    if not _IDENT.fullmatch(name):
        raise QueryRejectedError(f"unsupported table name: {name}")
    if catalog == "" and namespace == "":
        if name.casefold() not in cte_names:
            raise QueryRejectedError(
                f"qualify {name} as catalog.namespace.table or namespace.table"
            )
        return
    if namespace == "" or not _IDENT.fullmatch(namespace):
        raise QueryRejectedError("unsupported table name")
    if catalog == "":
        return
    if not _IDENT.fullmatch(catalog) or catalog.casefold() != default_catalog.casefold():
        raise QueryRejectedError(f"unknown catalog: {catalog}")
