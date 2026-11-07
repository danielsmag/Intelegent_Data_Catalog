"""Operator contract. A source names the asset. An operator is one deterministic step."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from intelegent_data_catalog.assets.definitions.models import OperatorRef, SourceRef
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.ports import (
    AirflowReader,
    GitHubReader,
    IcebergReader,
    TableQuery,
)


class StepSkipped(Exception):
    """The step has nothing new to write. The run still succeeds."""

    def __init__(self, reason: str) -> None:
        self.reason: str = reason
        super().__init__(reason)


class OperatorContext(ABC):
    """What an operator sees while it runs. The executor supplies the implementation."""

    def __init__(
        self,
        asset_id: UUID,
        asset_key: str,
        logger: logging.Logger,
        *,
        force: bool,
    ) -> None:
        self.asset_id: UUID = asset_id
        self.asset_key: str = asset_key
        self.logger: logging.Logger = logger
        self.force: bool = force
        self._task_id: str = ""

    def set_task(self, task_id: str) -> None:
        """Remember the step that is about to call ``write_output``."""
        self._task_id = task_id

    @abstractmethod
    def source(self) -> SourceRef:
        """Return the source stored on the asset."""

    @abstractmethod
    def head_sha(self) -> str | None:
        """Return the last GitHub commit this asset already snapshotted."""

    @abstractmethod
    def remember_head(self, sha: str) -> None:
        """Store ``sha`` so a later scheduled run can skip an unchanged branch."""

    @abstractmethod
    def write_output(self, name: str, markdown: str, data: JsonValue | None) -> None:
        """Store markdown and an optional JSON sidecar for the current step."""

    @abstractmethod
    def airflow(self) -> AirflowReader:
        """Return the Airflow reader for this asset's instance."""

    @abstractmethod
    def iceberg(self) -> IcebergReader:
        """Return the Iceberg metadata reader."""

    @abstractmethod
    def query(self) -> TableQuery:
        """Return the read-only SQL runner used for profiling."""

    @abstractmethod
    def github(self) -> GitHubReader:
        """Return the GitHub reader."""


class OperatorObserver(Protocol):
    """Optional hooks around each step of ``execute``."""

    def begin(self, task_id: str) -> None:
        """Mark ``task_id`` started."""

    def end(self, task_id: str, error: str | None) -> None:
        """Mark ``task_id`` finished. ``error`` is set when the step failed."""

    def skip(self, task_id: str, reason: str) -> None:
        """Mark ``task_id`` skipped. The run continues."""


class Operator(BaseModel, ABC):
    """One deterministic step. The class name and set fields are what gets stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: ClassVar[str]

    def to_ref(self) -> OperatorRef:
        """Return the serialized operator an executor can rebuild."""
        raw: object = self.model_dump(mode="json", include=set(self.model_fields_set))
        if not isinstance(raw, dict):
            raise AssetDefinitionError("operator fields must be an object")
        params: dict[str, JsonValue] = {}
        key: object
        value: object
        for key, value in raw.items():
            params[str(key)] = cast(JsonValue, value)
        return OperatorRef(type=type(self).__name__, params=params)

    @abstractmethod
    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Run this step."""


def _run_step(observer: OperatorObserver | None, task_id: str, action: Callable[[], None]) -> None:
    """Run one step and tell ``observer`` how it ended."""
    if observer is not None:
        observer.begin(task_id)
    try:
        action()
    except StepSkipped as skipped:
        if observer is not None:
            observer.skip(task_id, skipped.reason)
        return
    except Exception as err:
        if observer is not None:
            observer.end(task_id, _step_error(err))
        raise
    if observer is not None:
        observer.end(task_id, None)


def _step_error(err: Exception) -> str:
    message: str = str(err)
    if message == "":
        return type(err).__name__
    return message
