"""Serialize Spark and pandas frames for the notebook grid."""

import math
from decimal import Decimal

ROW_CAP: int = 500
CATALOG_MIME: str = "application/vnd.catalog.table+json"


def cell_value(value: object) -> object:
    """JSON-safe cell. NaN and missing values become null."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    item = _numpy_item(value)
    if item is not value:
        return cell_value(item)
    if isinstance(value, dict):
        rendered: dict[str, object] = {}
        key: object
        entry: object
        for key, entry in value.items():
            rendered[str(key)] = cell_value(entry)
        return rendered
    if isinstance(value, list | tuple):
        return [cell_value(entry) for entry in value]
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            text = iso()
        except TypeError:
            text = None
        if isinstance(text, str):
            return text
    return str(value)


def table_payload(
    columns: list[dict[str, str]],
    rows: list[list[object]],
    truncated: bool,
) -> dict[str, object]:
    """Build the mime body the notebook editor draws as a grid."""
    safe_rows: list[list[object]] = []
    row: list[object]
    for row in rows:
        safe_rows.append([cell_value(value) for value in row])
    return {"columns": columns, "rows": safe_rows, "truncated": truncated}


def _numpy_item(value: object) -> object:
    item = getattr(value, "item", None)
    if not callable(item):
        return value
    module = type(value).__module__
    if not module.startswith("numpy"):
        return value
    try:
        unpacked: object = item()
    except (TypeError, ValueError):
        return value
    return unpacked
