"""One read-only Spark statement of column statistics."""

import re

from intelegent_data_catalog.exceptions import CatalogError

_IDENT: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PRIMITIVE_TYPES: frozenset[str] = frozenset(
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
_NUMERIC: frozenset[str] = frozenset({"int", "long", "float", "double"})
_ORDERED: frozenset[str] = _NUMERIC | frozenset(
    {"string", "date", "timestamp", "timestamptz", "boolean"},
)


def is_identifier(value: str) -> bool:
    """Return whether ``value`` can be inlined into profile SQL."""
    return _IDENT.fullmatch(value) is not None


class ProfileSql:
    """Builds the aggregate statement for one table."""

    def statement(
        self,
        catalog: str,
        namespace: str,
        table: str,
        columns: list[tuple[str, str]],
    ) -> str:
        """Return one ``SELECT`` of row and column statistics."""
        _require_ident(catalog, "catalog")
        _require_ident(namespace, "namespace")
        _require_ident(table, "table")
        pieces: list[str] = ["COUNT(*) AS row_count"]
        name: str
        iceberg_type: str
        for name, iceberg_type in columns:
            _require_ident(name, "column")
            pieces.extend(_column_pieces(name, iceberg_type))
        select_list: str = ", ".join(pieces)
        return f"SELECT {select_list} FROM {catalog}.{namespace}.{table}"


def _column_pieces(name: str, iceberg_type: str) -> list[str]:
    pieces: list[str] = [
        f"SUM(CASE WHEN {name} IS NULL THEN 1 ELSE 0 END) AS {name}__nulls",
        f"approx_count_distinct({name}) AS {name}__distinct",
    ]
    if iceberg_type in _ORDERED:
        pieces.append(f"CAST(MIN({name}) AS STRING) AS {name}__min")
        pieces.append(f"CAST(MAX({name}) AS STRING) AS {name}__max")
    if iceberg_type in _NUMERIC:
        pieces.append(f"AVG({name}) AS {name}__mean")
        pieces.append(f"stddev({name}) AS {name}__stddev")
    if iceberg_type == "string":
        pieces.append(f"AVG(LENGTH({name})) AS {name}__avg_len")
        pieces.append(f"MAX(LENGTH({name})) AS {name}__max_len")
    if iceberg_type == "boolean":
        pieces.append(f"SUM(CASE WHEN {name} THEN 1 ELSE 0 END) AS {name}__true")
    return pieces


def _require_ident(value: str, label: str) -> None:
    if not is_identifier(value):
        raise CatalogError(f"cannot profile {label} `{value}`: name must be a simple identifier")
