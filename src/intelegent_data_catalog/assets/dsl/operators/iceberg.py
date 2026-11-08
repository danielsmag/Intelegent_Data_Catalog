"""Iceberg table and schema metadata, plus Spark profiles. No language model."""

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
    heading,
    join_sections,
    json_block,
    table,
)
from intelegent_data_catalog.assets.profiling.links import ColumnLinkInferrer
from intelegent_data_catalog.assets.profiling.models import (
    ColumnLink,
    ProfileDocument,
    ProfileScope,
    TableProfile,
)
from intelegent_data_catalog.assets.profiling.profiler import (
    IcebergTableProfiler,
    TableProfileResult,
)
from intelegent_data_catalog.assets.profiling.publish import ProfilePublisher
from intelegent_data_catalog.assets.profiling.sql import is_identifier
from intelegent_data_catalog.catalog.repositories.polaris import (
    IcebergField,
    IcebergSchema,
    format_iceberg_type,
)
from intelegent_data_catalog.catalog.repositories.polaris import (
    IcebergTableMetadata as IcebergMetadata,
)
from intelegent_data_catalog.exceptions import CatalogError


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
        """Count rows, summarize primitive columns, and write the profile graph."""

        def collect() -> None:
            ref: IcebergTableSourceRef = _table_source(context)
            result: TableProfileResult = IcebergTableProfiler(
                context.iceberg(),
                context.query(),
            ).profile(ref, self.max_columns)
            document: ProfileDocument = ProfileDocument(
                scope=ProfileScope.TABLE,
                scope_name=f"{ref.catalog}.{ref.namespace}.{ref.table}",
                tables=[result.table],
                columns_truncated=result.columns_truncated,
            )
            ProfilePublisher(context).publish(document)

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
    infer_links: bool = True

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Run one profile query per table and store the combined profile."""

        def collect() -> None:
            ref: IcebergSchemaSourceRef = _schema_source(context)
            names: list[str] = _selected(context, ref)
            shown: list[str] = names[: self.max_tables]
            profiler: IcebergTableProfiler = IcebergTableProfiler(
                context.iceberg(),
                context.query(),
            )
            tables: list[TableProfile] = []
            skipped: list[str] = []
            columns_truncated: bool = False
            name: str
            for name in shown:
                if not _ident(ref.catalog, ref.namespace, name):
                    skipped.append(name)
                    continue
                result: TableProfileResult = profiler.profile(
                    _table_ref(ref, name),
                    self.max_columns,
                )
                tables.append(result.table)
                columns_truncated = columns_truncated or result.columns_truncated
            links: list[ColumnLink] = []
            if self.infer_links:
                links = ColumnLinkInferrer().infer(tables)
            document: ProfileDocument = ProfileDocument(
                scope=ProfileScope.SCHEMA,
                scope_name=f"{ref.catalog}.{ref.namespace}",
                tables=tables,
                links=links,
                tables_truncated=len(names) > len(shown),
                columns_truncated=columns_truncated,
                skipped=skipped,
            )
            ProfilePublisher(context).publish(document)

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


def _ident(catalog: str, namespace: str, table: str) -> bool:
    return is_identifier(catalog) and is_identifier(namespace) and is_identifier(table)


def _note(total: int, shown: int) -> str:
    if total <= shown:
        return ""
    return f"Showing the first {shown} of {total} tables."
