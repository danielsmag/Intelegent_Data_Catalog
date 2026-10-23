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
    """Catalog table kind. Formats such as ICEBERG stay on ``table_format``."""

    TABLE = "TABLE"
    VIEW = "VIEW"


class AssetKind(StrEnum):
    """Kind of catalog asset. Detail rows live in the matching source table."""

    TABLE = "TABLE"
    AIRFLOW_DAG = "AIRFLOW_DAG"
    NOTEBOOK = "NOTEBOOK"
    FILE = "FILE"


class AssetStatus(StrEnum):
    """Observation state of an asset. Missing means the source no longer lists it."""

    ACTIVE = "ACTIVE"
    MISSING = "MISSING"
    DEPRECATED = "DEPRECATED"


class AssetRefreshMode(StrEnum):
    """When an asset rebuilds its context."""

    INTERVAL = "INTERVAL"
    ON_CHANGE = "ON_CHANGE"
    MANUAL = "MANUAL"


class TableStatus(StrEnum):
    """Observation or governance status of a catalog table."""

    ACTIVE = "ACTIVE"
    MISSING = "MISSING"
    DEPRECATED = "DEPRECATED"


class TagOrigin(StrEnum):
    """Who attached a tag. Imported tags do not replace USER tags."""

    USER = "USER"
    IMPORT = "IMPORT"


class ContextOrigin(StrEnum):
    """Who wrote the business context. ``USER`` wins until the next manual edit."""

    USER = "USER"
    LLM = "LLM"


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


class AirflowInstanceStatus(StrEnum):
    """Whether the catalog should call a registered Airflow instance."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AirflowMappingOrigin(StrEnum):
    """How a DAG was linked to a catalog table. Sync never rewrites MANUAL or LLM."""

    ASSET = "ASSET"
    SOURCE_MATCH = "SOURCE_MATCH"
    MANUAL = "MANUAL"
    LLM = "LLM"


class AirflowMappingDirection(StrEnum):
    """Whether the DAG reads or writes the catalog table. UNKNOWN is unresolved."""

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    UNKNOWN = "UNKNOWN"


class CatalogJobKind(StrEnum):
    """Which background build a catalog job row runs."""

    KNOWLEDGE = "knowledge"
    SCOPE = "scope"
    SPACE = "space"
    CONTEXT = "context"
    AIRFLOW = "airflow"
    ASSET = "asset"


class CatalogJobStatus(StrEnum):
    """Outcome stored for a background build. Matches the API job read."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RefreshSubjectKind(StrEnum):
    """Which catalog entity a saved configuration row belongs to."""

    SPACE = "space"
    SCOPE = "scope"
    TABLE = "table"
    ASSET = "asset"


class SpaceIsolationMode(StrEnum):
    """Who can see a space. ``OPEN`` is every workspace. ``ISOLATED`` is bound workspaces."""

    OPEN = "OPEN"
    ISOLATED = "ISOLATED"


class WorkspaceRole(StrEnum):
    """Role of a user inside one workspace."""

    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class WorkspaceNodeKind(StrEnum):
    """Folder or file in a workspace tree."""

    FOLDER = "FOLDER"
    FILE = "FILE"


class DefinitionKind(StrEnum):
    """Object a definition file is allowed to declare."""

    SPACE = "SPACE"
    SCOPE = "SCOPE"
    ASSET = "ASSET"


class DefinitionFileStatus(StrEnum):
    """Result of the last scan of one definition file."""

    OK = "OK"
    ERROR = "ERROR"
    REMOVED = "REMOVED"


class DefinitionItemStatus(StrEnum):
    """Whether one declared object was applied to the catalog."""

    APPLIED = "APPLIED"
    UNRESOLVED = "UNRESOLVED"
    ERROR = "ERROR"
    REMOVED = "REMOVED"


class DefinitionRunStatus(StrEnum):
    """State of the operator run recorded on one definition item."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OperatorRunTrigger(StrEnum):
    """Why an asset operator run started."""

    APPLY = "apply"
    SCHEDULE = "schedule"
    MANUAL = "manual"


class OperatorTaskState(StrEnum):
    """State of one task inside an asset operator run."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    UPSTREAM_FAILED = "upstream_failed"


class CapabilitySupport(StrEnum):
    """Whether a source binding claims a metadata feature.

    UNKNOWN is distinct from UNSUPPORTED. An unknown foreign-key capability is not
    an empty relationship set.
    """

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"
