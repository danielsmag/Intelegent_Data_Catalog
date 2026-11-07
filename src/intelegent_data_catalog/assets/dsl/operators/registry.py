"""Rebuild a stored operator. The class name is the registry key."""

from intelegent_data_catalog.assets.definitions.models import OperatorRef
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.airflow import AirflowMetadata
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.assets.dsl.operators.github import GitHubRepoSnapshot
from intelegent_data_catalog.assets.dsl.operators.iceberg import (
    IcebergSchemaMetadata,
    IcebergSchemaProfile,
    IcebergTableMetadata,
    IcebergTableProfile,
)

_OPERATORS: dict[str, type[Operator]] = {
    cls.__name__: cls
    for cls in (
        AirflowMetadata,
        IcebergTableMetadata,
        IcebergTableProfile,
        IcebergSchemaMetadata,
        IcebergSchemaProfile,
        GitHubRepoSnapshot,
    )
}


def operator_from_ref(ref: OperatorRef) -> Operator:
    """Rebuild one operator from the declaration."""
    cls: type[Operator] | None = _OPERATORS.get(ref.type)
    if cls is None:
        raise AssetDefinitionError(f"unknown operator: {ref.type}")
    return cls.model_validate(ref.params)
