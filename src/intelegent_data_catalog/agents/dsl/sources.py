"""Sources name what an agent is."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from intelegent_data_catalog.agents.definitions.models import (
    AirflowInstanceSourceRef,
    AirflowSourceRef,
    PageSourceRef,
    SchemaSourceRef,
    SourceRef,
    TableSourceRef,
)
from intelegent_data_catalog.persistence.enums import AgentKind
from intelegent_data_catalog.registry.identifiers import require_slug


class Source(BaseModel, ABC):
    """The catalog object an agent stands for. Construction does not read the catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @abstractmethod
    def to_ref(self) -> SourceRef:
        """Return the stored source reference."""

    @property
    @abstractmethod
    def kind(self) -> AgentKind | None:
        """Stored agent kind. ``None`` when apply decides, as a page does."""


class IcebergTable(Source):
    """One Iceberg table, named by source key."""

    source_key: str = Field(min_length=1, max_length=2048)
    data_source: str | None = None

    @model_validator(mode="after")
    def _data_source_not_blank(self) -> IcebergTable:
        _reject_blank_data_source(self.data_source)
        return self

    def to_ref(self) -> TableSourceRef:
        """Return the stored table reference."""
        return _table_ref(self.source_key, self.data_source)

    @property
    def kind(self) -> AgentKind:
        """Table agent."""
        return AgentKind.TABLE


class PostgresTable(Source):
    """One Postgres table, named by source key."""

    source_key: str = Field(min_length=1, max_length=2048)
    data_source: str | None = None

    @model_validator(mode="after")
    def _data_source_not_blank(self) -> PostgresTable:
        _reject_blank_data_source(self.data_source)
        return self

    def to_ref(self) -> TableSourceRef:
        """Return the stored table reference."""
        return _table_ref(self.source_key, self.data_source)

    @property
    def kind(self) -> AgentKind:
        """Table agent."""
        return AgentKind.TABLE


class Schema(Source):
    """Every table in one physical catalog and namespace, as one agent."""

    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    data_source: str | None = None
    include: list[str] | None = None
    exclude: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _data_source_not_blank(self) -> Schema:
        _reject_blank_data_source(self.data_source)
        return self

    @field_validator("include", "exclude")
    @classmethod
    def _clean_names(cls, value: list[str] | None) -> list[str] | None:
        return _table_names(value)

    def to_ref(self) -> SchemaSourceRef:
        """Return the stored schema reference, including the table filters."""
        data_source: str | None = None if self.data_source is None else self.data_source.strip()
        return SchemaSourceRef(
            catalog=self.catalog,
            namespace=self.namespace,
            data_source=data_source,
            include=self.include,
            exclude=list(self.exclude),
        )

    @property
    def kind(self) -> AgentKind:
        """Schema agent."""
        return AgentKind.SCHEMA


class AirflowDag(Source):
    """One synced Airflow DAG."""

    instance: str = Field(min_length=1, max_length=255)
    dag_id: str = Field(min_length=1, max_length=250)

    @model_validator(mode="after")
    def _instance_slug(self) -> AirflowDag:
        require_slug(self.instance)
        return self

    def to_ref(self) -> AirflowSourceRef:
        """Return the stored DAG reference."""
        return AirflowSourceRef(instance=self.instance, dag_id=self.dag_id)

    @property
    def kind(self) -> AgentKind:
        """DAG agent."""
        return AgentKind.AIRFLOW_DAG


class AirflowInstance(Source):
    """One registered Airflow instance."""

    instance: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def _instance_slug(self) -> AirflowInstance:
        require_slug(self.instance)
        return self

    def to_ref(self) -> AirflowInstanceSourceRef:
        """Return the stored instance reference."""
        return AirflowInstanceSourceRef(instance=self.instance)

    @property
    def kind(self) -> AgentKind:
        """Instance agent."""
        return AgentKind.AIRFLOW_INSTANCE


class Page(Source):
    """A workspace file or notebook. Apply chooses ``FILE`` or ``NOTEBOOK``."""

    path: str = Field(min_length=1, max_length=2048)
    workspace: str = "shared"

    @model_validator(mode="after")
    def _workspace_slug(self) -> Page:
        require_slug(self.workspace)
        return self

    def to_ref(self) -> PageSourceRef:
        """Return the stored page reference."""
        return PageSourceRef(workspace=self.workspace, path=self.path)

    @property
    def kind(self) -> None:
        """The file extension decides the kind at apply time."""
        return None


def _table_ref(source_key: str, data_source: str | None) -> TableSourceRef:
    cleaned: str | None = None if data_source is None else data_source.strip()
    return TableSourceRef(source_key=source_key, data_source=cleaned)


def _reject_blank_data_source(value: str | None) -> None:
    if value is not None and value.strip() == "":
        raise ValueError("data_source cannot be blank")


def _table_names(names: list[str] | None) -> list[str] | None:
    """Strip names, reject blanks and duplicates. ``None`` stays ``None``."""
    if names is None:
        return None
    found: list[str] = []
    seen: set[str] = set()
    name: str
    for name in names:
        cleaned: str = name.strip()
        if cleaned == "":
            raise ValueError("table name cannot be blank")
        if cleaned in seen:
            raise ValueError(f"table name is listed twice: {cleaned}")
        seen.add(cleaned)
        found.append(cleaned)
    return found
