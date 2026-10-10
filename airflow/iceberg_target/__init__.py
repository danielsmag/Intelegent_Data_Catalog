"""PyIceberg writer used by the standalone Airflow test DAGs."""

from iceberg_target.writer import IcebergTableWriter

__all__ = ["IcebergTableWriter"]
