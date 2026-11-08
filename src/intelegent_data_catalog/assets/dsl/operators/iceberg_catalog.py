"""Iceberg catalog metadata and a profile across its namespaces."""

from typing import ClassVar

from pydantic import JsonValue

from intelegent_data_catalog.assets.definitions.models import (
    IcebergCatalogSourceRef,
    IcebergTableSourceRef,
)
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.base import (
    Operator,
    OperatorContext,
    OperatorObserver,
    _run_step,
)
from intelegent_data_catalog.assets.dsl.operators.render import heading, join_sections, table
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
from intelegent_data_catalog.assets.profiling.selection import NameSelection
from intelegent_data_catalog.assets.profiling.sql import is_identifier


class IcebergCatalogMetadata(Operator):
    """Write the namespaces and tables in one Iceberg catalog."""

    task_id: ClassVar[str] = "iceberg_catalog_metadata"
    max_namespaces: int = 100
    max_tables: int = 50

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """List namespaces and their tables and store one markdown document."""

        def collect() -> None:
            ref: IcebergCatalogSourceRef = _catalog_source(context)
            names: list[str] = _namespaces(context, ref)
            shown: list[str] = names[: self.max_namespaces]
            sections: list[str] = [
                heading(1, ref.catalog),
                f"{len(names)} namespaces.",
                _note(len(names), len(shown), "namespaces"),
            ]
            listed: dict[str, JsonValue] = {}
            namespace: str
            for namespace in shown:
                tables: list[str] = context.iceberg().list_table_names(ref.catalog, namespace)
                kept: list[str] = tables[: self.max_tables]
                stored: list[JsonValue] = [name for name in kept]
                listed[namespace] = stored
                sections.append(heading(2, namespace))
                sections.append(_note(len(tables), len(kept), "tables"))
                sections.append(table(["table"], [[name] for name in kept]))
            context.write_output(
                "metadata",
                join_sections(sections),
                {"namespaces": listed, "truncated": len(names) > len(shown)},
            )

        _run_step(observer, self.task_id, collect)


class IcebergCatalogProfile(Operator):
    """Profile tables across one Iceberg catalog and infer column links."""

    task_id: ClassVar[str] = "iceberg_catalog_profile"
    max_namespaces: int = 50
    max_tables: int = 25
    max_columns: int = 20
    infer_links: bool = True

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Profile up to ``max_tables`` tables and store markdown plus the graph."""

        def collect() -> None:
            ref: IcebergCatalogSourceRef = _catalog_source(context)
            names: list[str] = _namespaces(context, ref)
            shown: list[str] = names[: self.max_namespaces]
            chosen: list[tuple[str, str]] = []
            skipped: list[str] = []
            namespace: str
            for namespace in shown:
                if not is_identifier(ref.catalog) or not is_identifier(namespace):
                    skipped.append(namespace)
                    continue
                table_name: str
                for table_name in context.iceberg().list_table_names(ref.catalog, namespace):
                    if not is_identifier(table_name):
                        skipped.append(f"{namespace}.{table_name}")
                        continue
                    chosen.append((namespace, table_name))
            profiled: list[tuple[str, str]] = chosen[: self.max_tables]
            profiler: IcebergTableProfiler = IcebergTableProfiler(
                context.iceberg(),
                context.query(),
            )
            tables: list[TableProfile] = []
            columns_truncated: bool = False
            pair: tuple[str, str]
            for pair in profiled:
                table_ref: IcebergTableSourceRef = IcebergTableSourceRef(
                    catalog=ref.catalog,
                    namespace=pair[0],
                    table=pair[1],
                )
                result: TableProfileResult = profiler.profile(table_ref, self.max_columns)
                tables.append(result.table)
                columns_truncated = columns_truncated or result.columns_truncated
            links: list[ColumnLink] = []
            if self.infer_links:
                links = ColumnLinkInferrer().infer(tables)
            document: ProfileDocument = ProfileDocument(
                scope=ProfileScope.CATALOG,
                scope_name=ref.catalog,
                tables=tables,
                links=links,
                tables_truncated=len(chosen) > len(profiled),
                columns_truncated=columns_truncated,
                namespaces_truncated=len(names) > len(shown),
                skipped=skipped,
            )
            ProfilePublisher(context).publish(document)

        _run_step(observer, self.task_id, collect)


def _catalog_source(context: OperatorContext) -> IcebergCatalogSourceRef:
    source: object = context.source()
    if not isinstance(source, IcebergCatalogSourceRef):
        raise AssetDefinitionError("Iceberg catalog operator requires an Iceberg catalog source")
    return source


def _namespaces(context: OperatorContext, ref: IcebergCatalogSourceRef) -> list[str]:
    names: list[str] = context.iceberg().list_namespaces(ref.catalog)
    return NameSelection().choose(names, ref.include, ref.exclude)


def _note(total: int, shown: int, label: str) -> str:
    if total <= shown:
        return ""
    return f"Showing the first {shown} of {total} {label}."
