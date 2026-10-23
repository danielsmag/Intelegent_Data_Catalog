"""Operators name the catalog source behind an asset and build its context when run."""

from intelegent_data_catalog.assets.dsl.operators.airflow import AirflowOperator
from intelegent_data_catalog.assets.dsl.operators.base import Operator, OperatorContext
from intelegent_data_catalog.assets.dsl.operators.page import PageOperator
from intelegent_data_catalog.assets.dsl.operators.registry import operator_from_ref
from intelegent_data_catalog.assets.dsl.operators.tables import (
    IcebergTableOperator,
    PostgresTableOperator,
    TableOperator,
)

__all__ = [
    "AirflowOperator",
    "IcebergTableOperator",
    "Operator",
    "OperatorContext",
    "PageOperator",
    "PostgresTableOperator",
    "TableOperator",
    "operator_from_ref",
]
