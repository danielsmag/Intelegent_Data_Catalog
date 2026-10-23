"""Airflow DAG operator."""

from pydantic import Field, model_validator

from intelegent_data_catalog.assets.definitions.models import AirflowSourceRef
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.registry.identifiers import require_slug


class AirflowOperator(Operator):
    """Link one synced Airflow DAG."""

    instance: str = Field(min_length=1, max_length=255)
    dag_id: str = Field(min_length=1, max_length=250)

    @model_validator(mode="after")
    def _instance_slug(self) -> AirflowOperator:
        require_slug(self.instance)
        return self

    def to_source(self) -> AirflowSourceRef:
        """Return the stored source reference."""
        return AirflowSourceRef(instance=self.instance, dag_id=self.dag_id)
