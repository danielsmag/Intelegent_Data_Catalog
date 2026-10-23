"""Operators name the catalog source behind an asset. They do not load it."""

from intelegent_data_catalog.assets.dsl.operators.airflow import AirflowOperator
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.assets.dsl.operators.page import PageOperator
from intelegent_data_catalog.assets.dsl.operators.tables import (
    IcebergTableOperator,
    PostgresTableOperator,
    TableOperator,
)

__all__ = [
    "AirflowOperator",
    "IcebergTableOperator",
    "Operator",
    "PageOperator",
    "PostgresTableOperator",
    "TableOperator",
]
