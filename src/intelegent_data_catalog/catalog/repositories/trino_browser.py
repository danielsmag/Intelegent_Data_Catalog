"""Trino catalog browser. Lists every user catalog, schema, and table."""

from collections.abc import Sequence

from pydantic import JsonValue

from intelegent_data_catalog.catalog.models import (
    CatalogNode,
    ColumnDetail,
    NamespaceNode,
    ObservedColumn,
    ObservedTable,
    QueryColumn,
    QueryResult,
    TableDetail,
    TableSummary,
)
from intelegent_data_catalog.exceptions import TableNotFoundError, TrinoUnavailableError
from intelegent_data_catalog.persistence.enums import TableKind
from intelegent_data_catalog.sources.trino.client import TrinoQuery
from intelegent_data_catalog.sources.trino.quoting import quote_trino_identifier, quote_trino_path

_SKIP_CATALOGS: frozenset[str] = frozenset({"system", "jmx"})
_SKIP_SCHEMAS: frozenset[str] = frozenset({"information_schema", "sys", "system"})


class TrinoCatalogBrowser:
    """Reads Trino metadata and row samples. It does not run the query editor."""

    def __init__(self, client: TrinoQuery) -> None:
        self._client: TrinoQuery = client

    def list_tree(self) -> list[CatalogNode]:
        """Return Trino catalogs, schemas, and table names."""
        catalogs: list[CatalogNode] = []
        catalog_name: str
        for catalog_name in self._catalog_names():
            namespaces: list[NamespaceNode] = []
            schema_name: str
            for schema_name in self._schema_names(catalog_name):
                tables: list[TableSummary] = [
                    TableSummary(name=table_name)
                    for table_name in self._table_names(catalog_name, schema_name)
                ]
                namespaces.append(NamespaceNode(name=schema_name, tables=tables))
            catalogs.append(CatalogNode(name=catalog_name, namespaces=namespaces))
        return catalogs

    def load_table(self, catalog: str, namespace: str, table: str) -> TableDetail:
        """Return columns reported by Trino for one table."""
        self._require_known(catalog, namespace, table)
        columns: list[ColumnDetail] = []
        observed: ObservedColumn
        for observed in self._columns(catalog, namespace, table):
            required: bool = observed.nullable is False
            columns.append(
                ColumnDetail(
                    name=observed.name,
                    type=observed.type_name,
                    required=required,
                    comment=observed.comment,
                )
            )
        return TableDetail(
            catalog=catalog,
            namespace=namespace,
            name=table,
            location=None,
            snapshot_id=None,
            last_updated_ms=None,
            columns=columns,
        )

    def list_observed(
        self,
        catalog: str,
        namespace: str,
        tables: list[str] | None,
    ) -> list[ObservedTable]:
        """Load columns and table kinds for one Trino schema."""
        available: set[str] = set(self._table_names(catalog, namespace))
        selected: list[str] = sorted(available) if tables is None else _unique(tables)
        kinds: dict[str, str] = self._table_kinds(catalog, namespace)
        observed: list[ObservedTable] = []
        table_name: str
        for table_name in selected:
            if table_name not in available:
                raise TableNotFoundError(catalog, namespace, table_name)
            observed.append(
                ObservedTable(
                    name=table_name,
                    kind=kinds.get(table_name, TableKind.TABLE.value),
                    columns=self._columns(catalog, namespace, table_name),
                )
            )
        return observed

    def connector_name(self, catalog: str) -> str:
        """Return the Trino connector for one catalog."""
        self._identifier(catalog)
        rows: list[dict[str, object]] = self._client.query(
            "SELECT connector_name FROM system.metadata.catalogs "
            f"WHERE catalog_name = '{_literal(catalog)}'"
        )
        if not rows:
            raise TrinoUnavailableError(f"Trino catalog not found: {catalog}")
        return _cell(rows[0], "connector_name")

    def sample(self, catalog: str, namespace: str, table: str, limit: int) -> QueryResult:
        """Return a limited Trino preview. The limit is a checked integer."""
        self._require_known(catalog, namespace, table)
        path: str = quote_trino_path([catalog, namespace, table])
        rows: list[dict[str, object]] = self._client.query(f"SELECT * FROM {path} LIMIT {limit}")
        return _query_result(rows)

    def _catalog_names(self) -> list[str]:
        rows: list[dict[str, object]] = self._client.query("SHOW CATALOGS")
        names: list[str] = []
        row: dict[str, object]
        for row in rows:
            name: str = _cell(row, "catalog")
            if name in _SKIP_CATALOGS:
                continue
            names.append(name)
        names.sort()
        return names

    def _schema_names(self, catalog: str) -> list[str]:
        rows: list[dict[str, object]] = self._client.query(
            f"SHOW SCHEMAS FROM {quote_trino_identifier(catalog)}"
        )
        names: list[str] = []
        row: dict[str, object]
        for row in rows:
            name: str = _cell(row, "schema")
            if name in _SKIP_SCHEMAS:
                continue
            names.append(name)
        names.sort()
        return names

    def _table_names(self, catalog: str, namespace: str) -> list[str]:
        rows: list[dict[str, object]] = self._client.query(
            f"SHOW TABLES FROM {quote_trino_path([catalog, namespace])}"
        )
        names: list[str] = []
        row: dict[str, object]
        for row in rows:
            names.append(_cell(row, "table"))
        names.sort()
        return names

    def _columns(self, catalog: str, namespace: str, table: str) -> list[ObservedColumn]:
        rows: list[dict[str, object]] = self._client.query(
            f"SHOW COLUMNS FROM {quote_trino_path([catalog, namespace, table])}"
        )
        columns: list[ObservedColumn] = []
        index: int
        row: dict[str, object]
        for index, row in enumerate(rows):
            extra: str = _optional_cell(row, "extra") or ""
            nullable: bool | None = None
            lowered: str = extra.lower()
            if "nullable" in lowered:
                nullable = True
            elif extra != "":
                nullable = False
            type_name: str = _optional_cell(row, "type") or "unknown"
            columns.append(
                ObservedColumn(
                    name=_cell(row, "column"),
                    type_name=type_name,
                    nullable=nullable,
                    comment=_optional_cell(row, "comment"),
                    ordinal_position=index,
                )
            )
        return columns

    def _table_kinds(self, catalog: str, namespace: str) -> dict[str, str]:
        sql: str = (
            "SELECT table_name, table_type "
            f"FROM {quote_trino_path([catalog, 'information_schema', 'tables'])} "
            f"WHERE table_schema = '{_literal(namespace)}'"
        )
        try:
            rows: list[dict[str, object]] = self._client.query(sql)
        except TrinoUnavailableError:
            return {}
        kinds: dict[str, str] = {}
        row: dict[str, object]
        for row in rows:
            kind: str = (_optional_cell(row, "table_type") or "").upper()
            table_name: str = _cell(row, "table_name")
            kinds[table_name] = TableKind.VIEW.value if "VIEW" in kind else TableKind.TABLE.value
        return kinds

    def _require_known(self, catalog: str, namespace: str, table: str) -> None:
        names: list[str] = self._table_names(catalog, namespace)
        if table not in names:
            raise TableNotFoundError(catalog, namespace, table)

    def _identifier(self, value: str) -> None:
        quote_trino_identifier(value)


def _unique(names: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    name: str
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _cell(row: dict[str, object], name: str) -> str:
    value: object = row.get(name)
    if isinstance(value, str) and value != "":
        return value
    if len(row) == 1:
        only: object = next(iter(row.values()))
        if isinstance(only, str):
            return only
    raise TrinoUnavailableError(f"Trino result is missing {name}")


def _optional_cell(row: dict[str, object], name: str) -> str | None:
    value: object = row.get(name)
    if value is None:
        return None
    text: str = str(value)
    if text == "" or text.lower() == "none":
        return None
    return text


def _literal(value: str) -> str:
    quote_trino_identifier(value)
    return value.replace("'", "''")


def _query_result(rows: Sequence[dict[str, object]]) -> QueryResult:
    if not rows:
        return QueryResult(columns=[], rows=[], truncated=False)
    first: dict[str, object] = rows[0]
    columns: list[QueryColumn] = [QueryColumn(name=name, type="varchar") for name in first]
    payload: list[list[JsonValue]] = []
    row: dict[str, object]
    for row in rows:
        payload.append([_json_cell(row.get(column.name)) for column in columns])
    return QueryResult(columns=columns, rows=payload, truncated=False)


def _json_cell(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
