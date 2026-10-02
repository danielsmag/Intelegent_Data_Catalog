"""Read Trino catalogs into discovery records. System schemas stay out unless asked."""

from intelegent_data_catalog.exceptions import TrinoUnavailableError
from intelegent_data_catalog.persistence.enums import CapabilitySupport, TableKind
from intelegent_data_catalog.sources.discovery import (
    DiscoveryBatch,
    DiscoveryRequest,
    ImportedForeignKey,
    SourceColumnRecord,
    SourceTableRecord,
)
from intelegent_data_catalog.sources.trino.client import TrinoQuery
from intelegent_data_catalog.sources.trino.quoting import quote_trino_identifier, quote_trino_path

_SYSTEM_SCHEMAS: frozenset[str] = frozenset({"information_schema", "sys", "system"})


class TrinoCatalogSource:
    """Metadata adapter. It does not provision connectors or run the query editor."""

    def __init__(self, client: TrinoQuery) -> None:
        self._client: TrinoQuery = client

    def discover(self, request: DiscoveryRequest) -> DiscoveryBatch:
        """List tables and columns for one approved Trino catalog."""
        schemas: list[str] = self._schemas(request)
        tables: list[SourceTableRecord] = []
        truncated: bool = False
        schema_name: str
        for schema_name in schemas:
            table_rows: list[dict[str, object]] = self._client.query(
                f"SHOW TABLES FROM {quote_trino_path([request.catalog, schema_name])}"
            )
            kinds: dict[str, TableKind] = self._table_kinds(request.catalog, schema_name)
            comments: dict[str, str | None] = self._table_comments(request.catalog, schema_name)
            row: dict[str, object]
            for row in table_rows:
                if len(tables) >= request.max_tables:
                    truncated = True
                    break
                table_name: str = _cell(row, "table")
                tables.append(
                    SourceTableRecord(
                        catalog=request.catalog,
                        namespace=schema_name,
                        name=table_name,
                        table_type=kinds.get(table_name, TableKind.TABLE),
                        source_description=comments.get(table_name),
                        columns=self._columns(request.catalog, schema_name, table_name),
                    )
                )
            if truncated:
                break
        capability: CapabilitySupport = self._relationship_capability(request.catalog)
        relationships: list[ImportedForeignKey] = []
        if capability is CapabilitySupport.SUPPORTED and not truncated:
            relationships = self._foreign_keys(request)
        return DiscoveryBatch(
            tables=tables,
            relationship_capability=capability,
            relationships=relationships,
            truncated=truncated,
        )

    def _schemas(self, request: DiscoveryRequest) -> list[str]:
        rows: list[dict[str, object]] = self._client.query(
            f"SHOW SCHEMAS FROM {quote_trino_identifier(request.catalog)}"
        )
        found: list[str] = []
        row: dict[str, object]
        for row in rows:
            name: str = _cell(row, "schema")
            if name in _SYSTEM_SCHEMAS and not request.include_system:
                continue
            if request.schema_allowlist and name not in request.schema_allowlist:
                continue
            found.append(name)
        return found

    def _columns(self, catalog: str, schema_name: str, table_name: str) -> list[SourceColumnRecord]:
        rows: list[dict[str, object]] = self._client.query(
            f"SHOW COLUMNS FROM {quote_trino_path([catalog, schema_name, table_name])}"
        )
        columns: list[SourceColumnRecord] = []
        index: int
        row: dict[str, object]
        for index, row in enumerate(rows):
            extra: str = _optional_cell(row, "extra") or ""
            nullable: bool | None = None
            if "nullable" in extra.lower():
                nullable = True
            elif extra != "":
                nullable = False
            comment: str | None = _optional_cell(row, "comment")
            columns.append(
                SourceColumnRecord(
                    name=_cell(row, "column"),
                    discovered_data_type=_optional_cell(row, "type"),
                    nullable=nullable,
                    ordinal_position=index,
                    source_description=comment,
                )
            )
        return columns

    def _table_kinds(self, catalog: str, schema_name: str) -> dict[str, TableKind]:
        sql: str = (
            "SELECT table_name, table_type "
            f"FROM {quote_trino_path([catalog, 'information_schema', 'tables'])} "
            f"WHERE table_schema = '{_literal(schema_name)}'"
        )
        try:
            rows: list[dict[str, object]] = self._client.query(sql)
        except TrinoUnavailableError:
            return {}
        kinds: dict[str, TableKind] = {}
        row: dict[str, object]
        for row in rows:
            kind: str = (_optional_cell(row, "table_type") or "").upper()
            table_name: str = _cell(row, "table_name")
            kinds[table_name] = TableKind.VIEW if "VIEW" in kind else TableKind.TABLE
        return kinds

    def _table_comments(self, catalog: str, schema_name: str) -> dict[str, str | None]:
        return {}

    def _relationship_capability(self, catalog: str) -> CapabilitySupport:
        sql: str = (
            "SELECT constraint_type "
            f"FROM {quote_trino_path([catalog, 'information_schema', 'table_constraints'])} "
            "WHERE constraint_type = 'FOREIGN KEY' LIMIT 1"
        )
        try:
            rows: list[dict[str, object]] = self._client.query(sql)
        except TrinoUnavailableError:
            return CapabilitySupport.UNSUPPORTED
        if rows:
            return CapabilitySupport.SUPPORTED
        return CapabilitySupport.UNKNOWN

    def _foreign_keys(self, request: DiscoveryRequest) -> list[ImportedForeignKey]:
        catalog: str = quote_trino_path([request.catalog, "information_schema"])
        sql: str = (
            "SELECT tc.constraint_name, tc.table_schema, tc.table_name, kcu.column_name, "
            "kcu.ordinal_position, ccu.table_schema AS target_schema, "
            "ccu.table_name AS target_table, ccu.column_name AS target_column "
            f"FROM {catalog}.table_constraints tc "
            f"JOIN {catalog}.key_column_usage kcu "
            "ON tc.constraint_schema = kcu.constraint_schema "
            "AND tc.constraint_name = kcu.constraint_name "
            f"JOIN {catalog}.constraint_column_usage ccu "
            "ON tc.constraint_schema = ccu.constraint_schema "
            "AND tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY'"
        )
        try:
            rows: list[dict[str, object]] = self._client.query(sql)
        except TrinoUnavailableError:
            return []
        keys: list[ImportedForeignKey] = []
        row: dict[str, object]
        for row in rows:
            namespace: str = _cell(row, "table_schema")
            if request.schema_allowlist and namespace not in request.schema_allowlist:
                continue
            position_text: str = _optional_cell(row, "ordinal_position") or "0"
            keys.append(
                ImportedForeignKey(
                    constraint_name=_cell(row, "constraint_name"),
                    source_namespace=namespace,
                    source_table=_cell(row, "table_name"),
                    source_column=_cell(row, "column_name"),
                    target_namespace=_cell(row, "target_schema"),
                    target_table=_cell(row, "target_table"),
                    target_column=_cell(row, "target_column"),
                    position=int(position_text),
                )
            )
        return keys


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
    """Escape a string literal. Identifiers use quote_trino_identifier instead."""
    return value.replace("'", "''")
