"""Airflow DAG operator."""

from __future__ import annotations

from pydantic import Field, model_validator

from intelegent_data_catalog.assets.definitions.models import (
    AirflowInstanceSourceRef,
    AirflowSourceRef,
)
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.assets.strategies.flags import DAG_FLAGS, INSTANCE_FLAGS
from intelegent_data_catalog.registry.identifiers import require_slug


class AirflowOperator(Operator):
    """Link one synced Airflow DAG. The ``include_*`` fields are its DAG build flags."""

    instance: str = Field(min_length=1, max_length=255)
    dag_id: str = Field(min_length=1, max_length=250)
    include_source: bool = DAG_FLAGS["include_source"]
    include_previous: bool = DAG_FLAGS["include_previous"]
    use_rules: bool = DAG_FLAGS["use_rules"]
    use_skills: bool = DAG_FLAGS["use_skills"]

    @model_validator(mode="after")
    def _instance_slug(self) -> AirflowOperator:
        require_slug(self.instance)
        return self

    def context_flags(self) -> dict[str, bool]:
        """DAG build flags."""
        flags: dict[str, bool] = {
            "include_source": self.include_source,
            "include_previous": self.include_previous,
        }
        flags.update(self._explicit_pack_flags())
        return flags

    def to_source(self) -> AirflowSourceRef:
        """Return the stored source reference."""
        return AirflowSourceRef(instance=self.instance, dag_id=self.dag_id)


class AirflowInstanceOperator(Operator):
    """Link one registered Airflow instance. The ``include_*`` fields are its build flags."""

    instance: str = Field(min_length=1, max_length=255)
    include_source: bool = INSTANCE_FLAGS["include_source"]
    include_previous: bool = INSTANCE_FLAGS["include_previous"]
    use_rules: bool = INSTANCE_FLAGS["use_rules"]
    use_skills: bool = INSTANCE_FLAGS["use_skills"]

    @model_validator(mode="after")
    def _instance_slug(self) -> AirflowInstanceOperator:
        require_slug(self.instance)
        return self

    def context_flags(self) -> dict[str, bool]:
        """Instance build flags."""
        flags: dict[str, bool] = {
            "include_source": self.include_source,
            "include_previous": self.include_previous,
        }
        flags.update(self._explicit_pack_flags())
        return flags

    def to_source(self) -> AirflowInstanceSourceRef:
        """Return the stored source reference."""
        return AirflowInstanceSourceRef(instance=self.instance)
