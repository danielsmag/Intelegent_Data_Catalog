"""Catalog-table operators."""

from intelegent_data_catalog.assets.dsl.operators.tables.base import TableOperator
from intelegent_data_catalog.assets.dsl.operators.tables.iceberg import IcebergTableOperator
from intelegent_data_catalog.assets.dsl.operators.tables.postgres import PostgresTableOperator

__all__ = [
    "IcebergTableOperator",
    "PostgresTableOperator",
    "TableOperator",
]
