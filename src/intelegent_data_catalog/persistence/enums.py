"""Stored catalog enums. Database checks use these values."""

from enum import StrEnum


class DataSourceType(StrEnum):
    """Kind of physical source. Independent of table format and query engine."""

    ICEBERG_REST = "ICEBERG_REST"
    POSTGRES = "POSTGRES"
    SNOWFLAKE = "SNOWFLAKE"
    BIGQUERY = "BIGQUERY"
    MYSQL = "MYSQL"


class TableKind(StrEnum):
    """Catalog asset kind. Formats such as ICEBERG stay on ``table_format``."""

    TABLE = "TABLE"
    VIEW = "VIEW"


class TableStatus(StrEnum):
    """Observation or governance status of a catalog table."""

    ACTIVE = "ACTIVE"
    MISSING = "MISSING"
    DEPRECATED = "DEPRECATED"


class TagOrigin(StrEnum):
    """Who attached a tag. Imported tags do not replace USER tags."""

    USER = "USER"
    IMPORT = "IMPORT"


class MetadataOrigin(StrEnum):
    """Where a column annotation was last recorded from."""

    USER = "USER"
    TRINO = "TRINO"
    NATIVE = "NATIVE"
    UNKNOWN = "UNKNOWN"


class RelationshipType(StrEnum):
    """Kind of catalog relationship. Inference is not a stored type."""

    FOREIGN_KEY = "FOREIGN_KEY"
    MANUAL = "MANUAL"


class RelationshipOrigin(StrEnum):
    """How a relationship entered the catalog."""

    DATABASE_CONSTRAINT = "DATABASE_CONSTRAINT"
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    LLM = "LLM"


class RelationshipStatus(StrEnum):
    """Review state of a relationship."""

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    DISABLED = "DISABLED"


class EngineType(StrEnum):
    """Execution or discovery engine. Addresses are not translated between engines."""

    TRINO = "TRINO"
    SPARK_SQL = "SPARK_SQL"


class EngineConnectionStatus(StrEnum):
    """Whether an engine connection is available for new bindings."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class BindingRole(StrEnum):
    """DISCOVERY uses Trino. QUERY uses Spark SQL."""

    DISCOVERY = "DISCOVERY"
    QUERY = "QUERY"


class BindingState(StrEnum):
    """Last address check. UNVALIDATED means no check has been stored."""

    UNVALIDATED = "UNVALIDATED"
    VALID = "VALID"
    UNAVAILABLE = "UNAVAILABLE"


class SyncRunStatus(StrEnum):
    """Metadata sync run outcome. PARTIAL and FAILED do not prove assets are gone."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class CapabilitySupport(StrEnum):
    """Whether a source binding claims a metadata feature.

    UNKNOWN is distinct from UNSUPPORTED. An unknown foreign-key capability is not
    an empty relationship set.
    """

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"
