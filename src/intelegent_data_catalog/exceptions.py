"""Project exception hierarchy."""


class CatalogError(Exception):
    """Base error for the data catalog."""


class SettingsError(CatalogError):
    """Raised when application settings cannot be loaded."""


class LoggingError(CatalogError):
    """Raised when logging cannot be configured."""


class ContextEngineError(CatalogError):
    """Raised when the context engine cannot scan the db folder."""


class SchedulerError(CatalogError):
    """Raised when the scan scheduler is configured incorrectly."""


class PolarisError(CatalogError):
    """Raised when Polaris cannot be queried."""


class TableNotFoundError(PolarisError):
    """Raised when a requested table does not exist in Polaris."""

    def __init__(self, catalog: str, namespace: str, table: str) -> None:
        self.catalog: str = catalog
        self.namespace: str = namespace
        self.table: str = table
        super().__init__(f"table not found: {catalog}.{namespace}.{table}")


class QueryRejectedError(CatalogError):
    """Raised when SQL is empty, too large, or not a read-only query."""


class QueryNotFoundError(CatalogError):
    """Raised when Spark cannot resolve a table named by a query."""


class QueryTimeoutError(CatalogError):
    """Raised when a Spark query exceeds the configured time limit."""


class SparkQueryError(CatalogError):
    """Raised when the Spark query service fails."""


class TrinoUnavailableError(CatalogError):
    """Raised when Trino metadata discovery cannot complete. The message is sanitized."""

    def __init__(self, message: str = "Trino is unavailable") -> None:
        super().__init__(message)


class GraphUnavailableError(CatalogError):
    """Raised when the Neo4j projection cannot be read or written."""

    def __init__(self, message: str = "graph projection is unavailable") -> None:
        super().__init__(message)


class EntityNotFoundError(CatalogError):
    """Raised when a catalog record id does not exist."""

    def __init__(self, entity: str, entity_id: str) -> None:
        self.entity: str = entity
        self.entity_id: str = entity_id
        super().__init__(f"{entity} not found: {entity_id}")


class ConflictError(CatalogError):
    """Raised when a write violates a catalog uniqueness or delete rule."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ValidationAppError(CatalogError):
    """Raised when a catalog write breaks a domain rule."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        self.field: str | None = field
        super().__init__(message)


class ProfileUnavailableError(CatalogError):
    """Raised when a data profile is requested and no profiler is configured."""

    def __init__(self, message: str = "data profiler is not configured") -> None:
        super().__init__(message)


class PersistenceUnavailableError(CatalogError):
    """Raised when the catalog database cannot be used. The message stays generic."""

    def __init__(self, message: str = "catalog database unavailable") -> None:
        super().__init__(message)
