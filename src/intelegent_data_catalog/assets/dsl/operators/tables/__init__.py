"""Catalog-table operators."""

from intelegent_data_catalog.assets.dsl.operators.tables.base import (
    TableContextOperator,
    TableOperator,
)
from intelegent_data_catalog.assets.dsl.operators.tables.iceberg import IcebergTableOperator
from intelegent_data_catalog.assets.dsl.operators.tables.postgres import PostgresTableOperator
from intelegent_data_catalog.assets.dsl.operators.tables.schema import SchemaOperator

__all__ = [
    "IcebergTableOperator",
    "PostgresTableOperator",
    "SchemaOperator",
    "TableContextOperator",
    "TableOperator",
]
