"""Infer candidate keys, references, and same-name columns from profile stats."""

from intelegent_data_catalog.assets.profiling.models import (
    ColumnLink,
    ColumnLinkKind,
    ColumnProfile,
    TableProfile,
    column_fqn,
)

_NUMERIC: frozenset[str] = frozenset({"int", "long", "float", "double"})
_TIME: frozenset[str] = frozenset({"timestamp", "timestamptz"})


class ColumnLinkInferrer:
    """Deterministic links across the tables in one profile."""

    def infer(self, tables: list[TableProfile]) -> list[ColumnLink]:
        """Return reference candidates, then same-name pairs not already linked."""
        keys: list[tuple[TableProfile, ColumnProfile]] = []
        columns: list[tuple[TableProfile, ColumnProfile]] = []
        table: TableProfile
        column: ColumnProfile
        for table in tables:
            for column in table.columns:
                columns.append((table, column))
                if column.candidate_key:
                    keys.append((table, column))
        references: list[ColumnLink] = self._references(columns, keys)
        linked: set[tuple[str, str]] = set()
        link: ColumnLink
        for link in references:
            linked.add(_pair(link.source, link.target))
        same_name: list[ColumnLink] = self._same_name(columns, linked)
        found: list[ColumnLink] = references + same_name
        found.sort(key=lambda item: (item.kind.value, item.source, item.target))
        return found

    def _references(
        self,
        columns: list[tuple[TableProfile, ColumnProfile]],
        keys: list[tuple[TableProfile, ColumnProfile]],
    ) -> list[ColumnLink]:
        found: list[ColumnLink] = []
        table: TableProfile
        column: ColumnProfile
        key_table: TableProfile
        key: ColumnProfile
        for table, column in columns:
            if column.candidate_key:
                continue
            for key_table, key in keys:
                if _same_table(table, key_table):
                    continue
                if not _compatible(column.iceberg_type, key.iceberg_type):
                    continue
                pattern: str | None = _name_pattern(column.name, key_table.table, key.name)
                if pattern is None:
                    continue
                if not _within(column, key):
                    continue
                score: float = _reference_score(pattern, column.iceberg_type, key.iceberg_type)
                source: str = column_fqn(table.catalog, table.namespace, table.table, column.name)
                target: str = column_fqn(
                    key_table.catalog,
                    key_table.namespace,
                    key_table.table,
                    key.name,
                )
                found.append(
                    ColumnLink(
                        source=source,
                        target=target,
                        kind=ColumnLinkKind.CANDIDATE_REFERENCE,
                        score=score,
                        reason=_reference_reason(pattern, column, key, key_table),
                    ),
                )
        return found

    def _same_name(
        self,
        columns: list[tuple[TableProfile, ColumnProfile]],
        linked: set[tuple[str, str]],
    ) -> list[ColumnLink]:
        found: list[ColumnLink] = []
        seen: set[tuple[str, str]] = set()
        left_index: int
        for left_index, (left_table, left) in enumerate(columns):
            right_index: int
            for right_index in range(left_index + 1, len(columns)):
                right_table: TableProfile
                right: ColumnProfile
                right_table, right = columns[right_index]
                if left.name != right.name:
                    continue
                if not _compatible(left.iceberg_type, right.iceberg_type):
                    continue
                if _same_table(left_table, right_table):
                    continue
                source: str = column_fqn(
                    left_table.catalog,
                    left_table.namespace,
                    left_table.table,
                    left.name,
                )
                target: str = column_fqn(
                    right_table.catalog,
                    right_table.namespace,
                    right_table.table,
                    right.name,
                )
                pair: tuple[str, str] = _pair(source, target)
                if pair in linked or pair in seen:
                    continue
                seen.add(pair)
                ordered_source: str
                ordered_target: str
                if source <= target:
                    ordered_source = source
                    ordered_target = target
                else:
                    ordered_source = target
                    ordered_target = source
                found.append(
                    ColumnLink(
                        source=ordered_source,
                        target=ordered_target,
                        kind=ColumnLinkKind.SAME_NAME,
                        score=0.4,
                        reason=f"same name and type {left.iceberg_type}",
                    ),
                )
        return found


def _same_table(left: TableProfile, right: TableProfile) -> bool:
    return (
        left.catalog == right.catalog
        and left.namespace == right.namespace
        and left.table == right.table
    )


def _compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    if left in _NUMERIC and right in _NUMERIC:
        return True
    return left in _TIME and right in _TIME


def _name_pattern(column: str, key_table: str, key_column: str) -> str | None:
    if column == key_column:
        return "same name"
    singular: str = _singular(key_table)
    if column == f"{singular}_{key_column}" or column == f"{key_table}_{key_column}":
        return f"{column} matches {key_table}.{key_column}"
    return None


def _singular(name: str) -> str:
    if len(name) > 4 and name.endswith(("sses", "shes", "ches", "xes", "zes")):
        return name[:-2]
    if len(name) > 3 and name.endswith("ies"):
        return name[:-3] + "y"
    if len(name) > 1 and name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _within(column: ColumnProfile, key: ColumnProfile) -> bool:
    left: tuple[float, float] | tuple[str, str] | None = _bounds(column.minimum, column.maximum)
    right: tuple[float, float] | tuple[str, str] | None = _bounds(key.minimum, key.maximum)
    if left is None or right is None:
        return False
    if isinstance(left[0], float) and isinstance(right[0], float):
        return left[0] >= right[0] and left[1] <= right[1]
    if isinstance(left[0], str) and isinstance(right[0], str):
        return left[0] >= right[0] and left[1] <= right[1]
    return False


def _bounds(
    minimum: str | None,
    maximum: str | None,
) -> tuple[float, float] | tuple[str, str] | None:
    if minimum is None or maximum is None or minimum == "" or maximum == "":
        return None
    try:
        return (float(minimum), float(maximum))
    except ValueError:
        return (minimum, maximum)


def _reference_score(pattern: str, source_type: str, target_type: str) -> float:
    score: float = 0.95 if pattern != "same name" else 0.8
    if source_type != target_type:
        score = round(score * 0.9, 2)
    return score


def _reference_reason(
    pattern: str,
    column: ColumnProfile,
    key: ColumnProfile,
    key_table: TableProfile,
) -> str:
    span: str = f"{column.minimum}..{column.maximum}"
    return (
        f"{pattern}; type {column.iceberg_type}; "
        f"values {span} inside {key_table.table}.{key.name} "
        f"{key.minimum}..{key.maximum}"
    )


def _pair(source: str, target: str) -> tuple[str, str]:
    if source <= target:
        return (source, target)
    return (target, source)
