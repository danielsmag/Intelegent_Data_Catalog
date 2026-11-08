"""Render a profile document as markdown."""

from intelegent_data_catalog.assets.dsl.operators.render import fence, heading, join_sections, table
from intelegent_data_catalog.assets.profiling.models import (
    ColumnLink,
    ColumnProfile,
    ProfileDocument,
    TableProfile,
)


class ProfileMarkdown:
    """Markdown snapshot stored next to the profile JSON."""

    def render(self, document: ProfileDocument) -> str:
        """Return the profile document as one markdown string."""
        sections: list[str] = [
            heading(1, f"Profile {document.scope_name}"),
            _summary(document),
            _notes(document),
        ]
        profile: TableProfile
        for profile in document.tables:
            sections.append(_table_section(profile))
        sections.append(_links(document.links))
        sections.append(_graph(document))
        return join_sections(sections)


def _summary(document: ProfileDocument) -> str:
    tables: int = len(document.tables)
    columns: int = sum(len(profile.columns) for profile in document.tables)
    keys: int = sum(
        1 for profile in document.tables for column in profile.columns if column.candidate_key
    )
    rows: int = sum(profile.row_count or 0 for profile in document.tables)
    return (
        f"{tables} tables, {rows} rows, {columns} columns, "
        f"{keys} candidate keys, {len(document.links)} links."
    )


def _notes(document: ProfileDocument) -> str:
    notes: list[str] = []
    if document.namespaces_truncated:
        notes.append("Namespace list truncated.")
    if document.tables_truncated:
        notes.append("Table list truncated.")
    if document.columns_truncated:
        notes.append("Column list truncated.")
    if len(document.skipped) > 0:
        notes.append("Skipped: " + ", ".join(document.skipped))
    return " ".join(notes)


def _table_section(profile: TableProfile) -> str:
    rows: list[list[str]] = [_column_row(column) for column in profile.columns]
    row_count: str = "" if profile.row_count is None else str(profile.row_count)
    return join_sections(
        [
            heading(2, f"Profile {profile.catalog}.{profile.namespace}.{profile.table}"),
            f"Rows: {row_count}",
            table(
                ["column", "type", "nulls", "null %", "distinct", "unique", "min", "max", "key"],
                rows,
            ),
            heading(3, "SQL"),
            fence("sql", profile.sql),
        ],
    )


def _column_row(column: ColumnProfile) -> list[str]:
    return [
        column.name,
        column.iceberg_type,
        _int(column.null_count),
        _ratio(column.null_ratio),
        _int(column.distinct_count),
        _ratio(column.uniqueness_ratio),
        column.minimum or "",
        column.maximum or "",
        "yes" if column.candidate_key else "",
    ]


def _links(links: list[ColumnLink]) -> str:
    rows: list[list[str]] = [
        [link.kind.value, link.source, link.target, f"{link.score:.2f}", link.reason]
        for link in links
    ]
    return join_sections(
        [
            heading(2, "Links"),
            table(["kind", "source", "target", "score", "reason"], rows),
        ],
    )


def _graph(document: ProfileDocument) -> str:
    if document.graph.written:
        return "Graph: written."
    error: str = document.graph.error or "not written"
    return f"Graph: not written ({error})."


def _int(value: int | None) -> str:
    if value is None:
        return ""
    return str(value)


def _ratio(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.1f}%"
