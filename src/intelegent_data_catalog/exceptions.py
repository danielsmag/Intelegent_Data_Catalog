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


class LlmError(CatalogError):
    """Raised when a language model call fails. The message stays generic."""

    def __init__(self, message: str = "language model is unavailable") -> None:
        super().__init__(message)


class LlmUnavailableError(LlmError):
    """Raised when a call needs a language model and none is configured."""

    def __init__(self, message: str = "language model is not configured") -> None:
        super().__init__(message)


class AirflowUnavailableError(CatalogError):
    """Raised when an Airflow REST call cannot complete. The message is sanitized."""

    def __init__(self, message: str = "Airflow is unavailable") -> None:
        super().__init__(message)


class AirflowAuthError(AirflowUnavailableError):
    """Raised when Airflow rejects the stored username or password."""

    def __init__(self, message: str = "Airflow rejected the credentials") -> None:
        super().__init__(message)


class GitHubUnavailableError(CatalogError):
    """Raised when a GitHub REST call cannot complete. The message is sanitized."""

    def __init__(self, message: str = "GitHub is unavailable") -> None:
        super().__init__(message)


class GitHubNotFoundError(GitHubUnavailableError):
    """Raised when GitHub has no repository for the owner and name."""

    def __init__(self, owner: str, repo: str) -> None:
        self.owner: str = owner
        self.repo: str = repo
        super().__init__(f"GitHub repository not found: {owner}/{repo}")


class AuthenticationError(CatalogError):
    """Raised when a request has no valid bearer token."""

    def __init__(self, message: str = "authentication required") -> None:
        super().__init__(message)


class AuthorizationError(CatalogError):
    """Raised when an authenticated user lacks a role or membership."""

    def __init__(self, message: str = "not allowed") -> None:
        super().__init__(message)


class PayloadTooLargeError(CatalogError):
    """Raised when an upload exceeds the configured size."""

    def __init__(self, message: str = "upload is too large") -> None:
        super().__init__(message)


class NotebookDisabledError(CatalogError):
    """Raised when a notebook kernel is requested and kernels are turned off."""

    def __init__(self, message: str = "notebook kernels are disabled") -> None:
        super().__init__(message)


class NotebookGatewayError(CatalogError):
    """Raised when the notebook kernel gateway cannot run a request."""

    def __init__(self, message: str = "notebook kernel gateway is unavailable") -> None:
        super().__init__(message)


class PersistenceUnavailableError(CatalogError):
    """Raised when the catalog database cannot be used. The message stays generic."""

    def __init__(self, message: str = "catalog database unavailable") -> None:
        super().__init__(message)
