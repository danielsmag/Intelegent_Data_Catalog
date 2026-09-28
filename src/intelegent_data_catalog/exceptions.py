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
