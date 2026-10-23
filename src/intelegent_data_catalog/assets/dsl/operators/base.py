"""Operator contract. A definition file names a source; an executor runs the operator.

Construction only records arguments. ``execute`` runs later, once the asset row
exists, and is what creates the asset's context.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from intelegent_data_catalog.assets.definitions.models import OperatorRef, SourceRef
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.execution.tasks import BUILD_CONTEXT, DESCRIBE


class OperatorContext(ABC):
    """What an operator sees while it runs. The executor supplies the implementation."""

    def __init__(self, asset_id: UUID, asset_name: str, logger: logging.Logger) -> None:
        self.asset_id: UUID = asset_id
        self.asset_name: str = asset_name
        self.logger: logging.Logger = logger

    @abstractmethod
    def build_context(self, flags: dict[str, bool]) -> None:
        """Build and save the asset's knowledge context with these build flags."""

    @abstractmethod
    def describe(self) -> None:
        """Write the asset's business context. A manual edit by a user is kept."""


class OperatorObserver(Protocol):
    """Optional hooks around each step of ``execute``."""

    def begin(self, task_id: str) -> None:
        """Mark ``task_id`` started."""

    def end(self, task_id: str, error: str | None) -> None:
        """Mark ``task_id`` finished. ``error`` is set when the step failed."""


class Operator(BaseModel, ABC):
    """Names the catalog source behind an asset and builds its context when executed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @abstractmethod
    def to_source(self) -> SourceRef:
        """Return the stored source reference."""

    def to_ref(self) -> OperatorRef:
        """Return the serialized operator an executor can rebuild."""
        raw: object = self.model_dump(mode="json")
        if not isinstance(raw, dict):
            raise AssetDefinitionError("operator fields must be an object")
        params: dict[str, JsonValue] = {}
        key: object
        value: object
        for key, value in raw.items():
            params[str(key)] = cast(JsonValue, value)
        return OperatorRef(type=type(self).__name__, params=params)

    def context_flags(self) -> dict[str, bool]:
        """Build flags for this asset kind. Subclasses expose them as fields."""
        return {}

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Create the asset's context: build the knowledge snapshot, then describe it."""
        flags: dict[str, bool] = self.context_flags()

        def build() -> None:
            context.build_context(flags)

        _run_step(observer, BUILD_CONTEXT, build)
        _run_step(observer, DESCRIBE, context.describe)


def _run_step(observer: OperatorObserver | None, task_id: str, action: Callable[[], None]) -> None:
    """Run one step and tell ``observer`` how it ended."""
    if observer is not None:
        observer.begin(task_id)
    try:
        action()
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
