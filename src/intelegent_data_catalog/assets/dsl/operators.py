"""Operators name the catalog source behind an asset. They do not load it."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from intelegent_data_catalog.assets.definitions.models import (
    AirflowSourceRef,
    PageSourceRef,
    TableSourceRef,
)
from intelegent_data_catalog.registry.identifiers import require_slug


class TableOperator(BaseModel):
    """Link a catalog table by its source key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_key: str = Field(min_length=1, max_length=2048)
    data_source: str | None = None

    @model_validator(mode="after")
    def _data_source_not_blank(self) -> TableOperator:
        if self.data_source is not None and self.data_source.strip() == "":
            raise ValueError("data_source cannot be blank")
        return self

    def to_source(self) -> TableSourceRef:
        """Return the stored source reference."""
        data_source: str | None = None if self.data_source is None else self.data_source.strip()
        return TableSourceRef(source_key=self.source_key, data_source=data_source)


class AirflowOperator(BaseModel):
    """Link one synced Airflow DAG."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instance: str = Field(min_length=1, max_length=255)
    dag_id: str = Field(min_length=1, max_length=250)

    @model_validator(mode="after")
    def _instance_slug(self) -> AirflowOperator:
        require_slug(self.instance)
        return self

    def to_source(self) -> AirflowSourceRef:
        """Return the stored source reference."""
        return AirflowSourceRef(instance=self.instance, dag_id=self.dag_id)


class PageOperator(BaseModel):
    """Link a workspace file or notebook."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=2048)
    workspace: str = "shared"

    @model_validator(mode="after")
    def _workspace_slug(self) -> PageOperator:
        require_slug(self.workspace)
        return self

    def to_source(self) -> PageSourceRef:
        """Return the stored source reference."""
        return PageSourceRef(workspace=self.workspace, path=self.path)
