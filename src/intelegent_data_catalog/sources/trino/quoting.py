"""Quote Trino identifiers. Values stay in parameters, not in the SQL text."""


def quote_trino_identifier(identifier: str) -> str:
    """Return a double-quoted Trino identifier without changing case."""
    if identifier == "" or identifier != identifier.strip() or "\x00" in identifier:
        raise ValueError("invalid Trino identifier")
    if len(identifier) > 512:
        raise ValueError("Trino identifier is too long")
    escaped: str = identifier.replace('"', '""')
    return f'"{escaped}"'


def quote_trino_path(parts: list[str]) -> str:
    """Quote each part of a catalog.schema.table path."""
    quoted: list[str] = [quote_trino_identifier(part) for part in parts]
    return ".".join(quoted)
