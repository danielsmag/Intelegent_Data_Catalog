"""Read PostgreSQL foreign keys when Trino does not report that capability."""

import psycopg

from intelegent_data_catalog.persistence.enums import CapabilitySupport, DataSourceType
from intelegent_data_catalog.persistence.models import DataSourceRow
from intelegent_data_catalog.sources.discovery import (
    DiscoveryRequest,
    EnrichmentBatch,
    ImportedForeignKey,
)

_FOREIGN_KEYS: str = """
SELECT
    tc.constraint_name,
    kcu.table_schema,
    kcu.table_name,
    kcu.column_name,
    kcu.ordinal_position,
    ccu.table_schema AS target_schema,
    ccu.table_name AS target_table,
    ccu.column_name AS target_column
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
    AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
ORDER BY tc.constraint_name, kcu.ordinal_position
"""

_NATIVE_TYPES: str = """
SELECT table_schema, table_name, column_name, udt_name
FROM information_schema.columns
WHERE table_schema <> 'information_schema' AND table_schema <> 'pg_catalog'
"""


class PostgresForeignKeyEnricher:
    """Native reader for POSTGRES sources. A connection failure is not an empty key set."""

    def enrich(self, source: DataSourceRow, request: DiscoveryRequest) -> EnrichmentBatch | None:
        """Return keys when the source answers. None means the source was not asked."""
        if source.type != DataSourceType.POSTGRES.value:
            return None
        password: str | None = _env_secret(source.credential_ref)
        host: str | None = _text(source.configuration.get("host"))
        database: str | None = _text(source.configuration.get("database"))
        user: str | None = _text(source.configuration.get("username"))
        if password is None or host is None or database is None or user is None:
            return None
        port_value: object = source.configuration.get("port", 5432)
        port: int = port_value if isinstance(port_value, int) else 5432
        try:
            with (
                psycopg.connect(
                    host=host,
                    port=port,
                    dbname=database,
                    user=user,
                    password=password,
                    connect_timeout=5,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(_FOREIGN_KEYS)
                key_rows: list[tuple[object, ...]] = list(cursor.fetchall())
                cursor.execute(_NATIVE_TYPES)
                type_rows: list[tuple[object, ...]] = list(cursor.fetchall())
        except psycopg.Error:
            return None
        keys: list[ImportedForeignKey] = []
        row: tuple[object, ...]
        for row in key_rows:
            namespace: str = str(row[1])
            if request.schema_allowlist and namespace not in request.schema_allowlist:
                continue
            keys.append(
                ImportedForeignKey(
                    constraint_name=str(row[0]),
                    source_namespace=namespace,
                    source_table=str(row[2]),
                    source_column=str(row[3]),
                    target_namespace=str(row[5]),
                    target_table=str(row[6]),
                    target_column=str(row[7]),
                    position=int(str(row[4])) - 1,
                )
            )
        native_types: dict[str, str] = {}
        for row in type_rows:
            native_types[f"{row[0]}.{row[1]}.{row[2]}"] = str(row[3])
        return EnrichmentBatch(
            relationship_capability=CapabilitySupport.SUPPORTED,
            relationships=keys,
            native_types=native_types,
        )


def _env_secret(credential_ref: str | None) -> str | None:
    if credential_ref is None or not credential_ref.startswith("env:"):
        return None
    import os

    return os.environ.get(credential_ref.removeprefix("env:"))


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip() != "":
        return value
    return None
