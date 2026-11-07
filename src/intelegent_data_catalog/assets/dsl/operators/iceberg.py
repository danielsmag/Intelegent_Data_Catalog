"""Iceberg table and schema metadata, plus optional Spark profiles. No language model."""

import re
from typing import ClassVar

from pydantic import JsonValue, ValidationError

from intelegent_data_catalog.assets.definitions.models import (
    IcebergSchemaSourceRef,
    IcebergTableSourceRef,
)
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.base import (
    Operator,
    OperatorContext,
    OperatorObserver,
    _run_step,
)
from intelegent_data_catalog.assets.dsl.operators.render import (
    as_json,
    heading,
    join_sections,
    json_block,
    table,
)
from intelegent_data_catalog.catalog.models import QueryResult
from intelegent_data_catalog.catalog.repositories.polaris import (
    IcebergField,
    IcebergSchema,
    format_iceberg_type,
)
from intelegent_data_catalog.catalog.repositories.polaris import (
    IcebergTableMetadata as IcebergMetadata,
)
from intelegent_data_catalog.exceptions import CatalogError

_IDENT: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PRIMITIVE: frozenset[str] = frozenset(
    {
        "boolean",
        "int",
        "long",
        "float",
        "double",
        "string",
        "date",
        "timestamp",
        "timestamptz",
        "binary",
    },
)


class IcebergTableMetadata(Operator):
    """Write the Iceberg REST load-table document for one table."""

    task_id: ClassVar[str] = "iceberg_table_metadata"

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Load the table and store ``metadata`` markdown plus the raw document."""

        def collect() -> None:
            ref: IcebergTableSourceRef = _table_source(context)
            raw: dict[str, JsonValue] = context.iceberg().load_table_raw(
                ref.catalog,
                ref.namespace,
                ref.table,
            )
            context.write_output("metadata", _table_markdown(ref, raw), raw)

        _run_step(observer, self.task_id, collect)


class IcebergTableProfile(Operator):
    """Profile one Iceberg table with read-only Spark SQL."""

    task_id: ClassVar[str] = "iceberg_table_profile"
    max_columns: int = 40

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Count rows and summarize primitive columns."""

        def collect() -> None:
            ref: IcebergTableSourceRef = _table_source(context)
            raw: dict[str, JsonValue] = context.iceberg().load_table_raw(
                ref.catalog,
                ref.namespace,
                ref.table,
            )
            columns: list[str] = _profile_columns(_metadata(raw))[: self.max_columns]
            sql: str = _profile_sql(ref.catalog, ref.namespace, ref.table, columns)
            result: QueryResult = context.query().query(sql)
            data: dict[str, JsonValue] = {
                "sql": sql,
                "columns": [column.name for column in result.columns],
                "rows": as_json(result.rows),
            }
            context.write_output("profile", _profile_markdown(ref, columns, result), data)

        _run_step(observer, self.task_id, collect)


class IcebergSchemaMetadata(Operator):
    """Write Iceberg metadata for every table in one namespace."""

    task_id: ClassVar[str] = "iceberg_schema_metadata"
    max_tables: int = 100

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Load each selected table and store one combined markdown document."""

        def collect() -> None:
            ref: IcebergSchemaSourceRef = _schema_source(context)
            names: list[str] = _selected(context, ref)
            shown: list[str] = names[: self.max_tables]
            documents: dict[str, JsonValue] = {}
            sections: list[str] = [
                heading(1, f"{ref.catalog}.{ref.namespace}"),
                f"{len(names)} tables.",
                _note(len(names), len(shown)),
            ]
            name: str
            for name in shown:
                raw: dict[str, JsonValue] = context.iceberg().load_table_raw(
                    ref.catalog,
                    ref.namespace,
                    name,
                )
                documents[name] = raw
                sections.append(_table_markdown(_table_ref(ref, name), raw))
            context.write_output(
                "metadata",
                join_sections(sections),
                {"tables": documents, "truncated": len(names) > len(shown)},
            )

        _run_step(observer, self.task_id, collect)


class IcebergSchemaProfile(Operator):
    """Profile every selected table in one Iceberg namespace."""

    task_id: ClassVar[str] = "iceberg_schema_profile"
    max_tables: int = 25
    max_columns: int = 20

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Run one profile query per table and store the combined markdown."""

        def collect() -> None:
            ref: IcebergSchemaSourceRef = _schema_source(context)
            names: list[str] = _selected(context, ref)[: self.max_tables]
            sections: list[str] = [heading(1, f"Profile {ref.catalog}.{ref.namespace}")]
            stored: dict[str, JsonValue] = {}
            name: str
            for name in names:
                raw: dict[str, JsonValue] = context.iceberg().load_table_raw(
                    ref.catalog,
                    ref.namespace,
                    name,
                )
                columns: list[str] = _profile_columns(_metadata(raw))[: self.max_columns]
                sql: str = _profile_sql(ref.catalog, ref.namespace, name, columns)
                result: QueryResult = context.query().query(sql)
                table_ref: IcebergTableSourceRef = _table_ref(ref, name)
                sections.append(_profile_markdown(table_ref, columns, result))
                stored[name] = {"sql": sql, "rows": as_json(result.rows)}
            context.write_output("profile", join_sections(sections), stored)

        _run_step(observer, self.task_id, collect)


def _table_source(context: OperatorContext) -> IcebergTableSourceRef:
    source: object = context.source()
    if not isinstance(source, IcebergTableSourceRef):
        raise AssetDefinitionError("Iceberg table operator requires an Iceberg table source")
    return source


def _schema_source(context: OperatorContext) -> IcebergSchemaSourceRef:
    source: object = context.source()
    if not isinstance(source, IcebergSchemaSourceRef):
        raise AssetDefinitionError("Iceberg schema operator requires an Iceberg schema source")
    return source


def _selected(context: OperatorContext, ref: IcebergSchemaSourceRef) -> list[str]:
    names: list[str] = context.iceberg().list_table_names(ref.catalog, ref.namespace)
    include: set[str] | None = None if ref.include is None else set(ref.include)
    exclude: set[str] = set(ref.exclude)
    chosen: list[str] = []
    name: str
    for name in names:
        if include is not None and len(include) > 0 and name not in include:
            continue
        if name in exclude:
            continue
        chosen.append(name)
    return chosen


def _table_ref(ref: IcebergSchemaSourceRef, table: str) -> IcebergTableSourceRef:
    return IcebergTableSourceRef(
        catalog=ref.catalog,
        namespace=ref.namespace,
        table=table,
    )


def _metadata(raw: dict[str, JsonValue]) -> IcebergMetadata:
    payload: JsonValue | None = raw.get("metadata")
    try:
        return IcebergMetadata.model_validate(payload)
    except ValidationError as err:
        raise CatalogError("Iceberg metadata did not include a schema") from err


def _current_schema(metadata: IcebergMetadata) -> IcebergSchema | None:
    if len(metadata.schemas) == 0:
        return None
    if metadata.current_schema_id is None:
        return metadata.schemas[-1]
    schema: IcebergSchema
    for schema in metadata.schemas:
        if schema.schema_id == metadata.current_schema_id:
            return schema
    return metadata.schemas[-1]


def _table_markdown(ref: IcebergTableSourceRef, raw: dict[str, JsonValue]) -> str:
    metadata: IcebergMetadata = _metadata(raw)
    schema: IcebergSchema | None = _current_schema(metadata)
    rows: list[list[str]] = []
    if schema is not None:
        field: IcebergField
        for field in schema.fields:
            rows.append(
                [
                    field.name,
                    format_iceberg_type(field.field_type),
                    "yes" if field.required else "no",
                    field.doc or "",
                ],
            )
    return join_sections(
        [
            heading(2, f"{ref.catalog}.{ref.namespace}.{ref.table}"),
            f"Location: {metadata.location or '(unknown)'}",
            f"Snapshot: {metadata.current_snapshot_id or '(none)'}",
            table(["column", "type", "required", "doc"], rows),
            heading(3, "Load table"),
            json_block(raw),
        ],
    )


def _profile_columns(metadata: IcebergMetadata) -> list[str]:
    schema: IcebergSchema | None = _current_schema(metadata)
    if schema is None:
        return []
    found: list[str] = []
    field: IcebergField
    for field in schema.fields:
        if not isinstance(field.field_type, str) or field.field_type not in _PRIMITIVE:
            continue
        if _IDENT.fullmatch(field.name) is None:
            continue
        found.append(field.name)
    return found


def _profile_sql(catalog: str, namespace: str, table: str, columns: list[str]) -> str:
    _require_ident(catalog, "catalog")
    _require_ident(namespace, "namespace")
    _require_ident(table, "table")
    pieces: list[str] = ["COUNT(*) AS row_count"]
    column: str
    for column in columns:
        pieces.append(f"SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS {column}__nulls")
        pieces.append(f"approx_count_distinct({column}) AS {column}__distinct")
        pieces.append(f"CAST(MIN({column}) AS STRING) AS {column}__min")
        pieces.append(f"CAST(MAX({column}) AS STRING) AS {column}__max")
    select_list: str = ", ".join(pieces)
    return f"SELECT {select_list} FROM {catalog}.{namespace}.{table}"


def _profile_markdown(
    ref: IcebergTableSourceRef,
    columns: list[str],
    result: QueryResult,
) -> str:
    if len(result.rows) == 0:
        return join_sections([heading(2, ref.table), "Profile returned no rows."])
    values: dict[str, str] = {}
    index: int
    for index, column in enumerate(result.columns):
        cell: JsonValue = result.rows[0][index] if index < len(result.rows[0]) else None
        values[column.name] = "" if cell is None else str(cell)
    rows: list[list[str]] = [
        [
            column,
            values.get(f"{column}__nulls", ""),
            values.get(f"{column}__distinct", ""),
            values.get(f"{column}__min", ""),
            values.get(f"{column}__max", ""),
        ]
        for column in columns
    ]
    return join_sections(
        [
            heading(2, f"Profile {ref.catalog}.{ref.namespace}.{ref.table}"),
            f"Rows: {values.get('row_count', '')}",
            table(["column", "nulls", "approx distinct", "min", "max"], rows),
        ],
    )


def _require_ident(value: str, label: str) -> None:
    if _IDENT.fullmatch(value) is None:
        raise CatalogError(f"cannot profile {label} `{value}`: name must be a simple identifier")


def _note(total: int, shown: int) -> str:
    if total <= shown:
        return ""
    return f"Showing the first {shown} of {total} tables."
