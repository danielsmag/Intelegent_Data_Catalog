"""Errors raised while appending rows to an existing Iceberg table."""


class IcebergTargetError(Exception):
    """Base error for the Airflow Iceberg writer."""


class IcebergConfigError(IcebergTargetError):
    """Raised when a required catalog setting is missing."""

    def __init__(self, name: str) -> None:
        self.name: str = name
        super().__init__(f"missing environment variable {name}")


class IcebergWriteError(IcebergTargetError):
    """Raised when an existing Iceberg table cannot be updated."""

    def __init__(self, table_name: str, message: str) -> None:
        self.table_name: str = table_name
        super().__init__(f"{table_name}: {message}")
