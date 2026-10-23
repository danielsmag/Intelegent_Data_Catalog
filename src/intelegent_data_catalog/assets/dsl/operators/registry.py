"""Rebuild an operator from its stored reference."""

from pydantic import ValidationError

from intelegent_data_catalog.assets.definitions.models import OperatorRef
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.airflow import AirflowOperator
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.assets.dsl.operators.page import PageOperator
from intelegent_data_catalog.assets.dsl.operators.tables import (
    IcebergTableOperator,
    PostgresTableOperator,
)

_OPERATORS: dict[str, type[Operator]] = {
    cls.__name__: cls
    for cls in (
        IcebergTableOperator,
        PostgresTableOperator,
        AirflowOperator,
        PageOperator,
    )
}


def operator_from_ref(ref: OperatorRef) -> Operator:
    """Return the operator ``ref`` names. Unknown names and bad fields raise."""
    cls: type[Operator] | None = _OPERATORS.get(ref.type)
    if cls is None:
        raise AssetDefinitionError(f"unknown operator: {ref.type}")
    try:
        return cls.model_validate(ref.params)
    except ValidationError as err:
        raise AssetDefinitionError(f"invalid {ref.type} fields") from err
