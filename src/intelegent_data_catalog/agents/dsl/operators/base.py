"""Operator contract. A source names the agent. An operator is one step that runs on it.

Construction only records arguments. ``execute`` runs later, once the agent row
exists.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from intelegent_data_catalog.agents.definitions.models import OperatorRef
from intelegent_data_catalog.agents.dsl.errors import AgentDefinitionError


class OperatorContext(ABC):
    """What an operator sees while it runs. The executor supplies the implementation."""

    def __init__(self, agent_id: UUID, agent_name: str, logger: logging.Logger) -> None:
        self.agent_id: UUID = agent_id
        self.agent_name: str = agent_name
        self.logger: logging.Logger = logger

    @abstractmethod
    def build_context(self, flags: dict[str, bool]) -> None:
        """Build and save the agent's knowledge context with these build flags."""

    @abstractmethod
    def describe(self) -> None:
        """Write the agent's business context. A manual edit by a user is kept."""

    @abstractmethod
    def has_context(self) -> bool:
        """True when the agent already has a non-blank business context."""


class OperatorObserver(Protocol):
    """Optional hooks around each step of ``execute``."""

    def begin(self, task_id: str) -> None:
        """Mark ``task_id`` started."""

    def end(self, task_id: str, error: str | None) -> None:
        """Mark ``task_id`` finished. ``error`` is set when the step failed."""


class Operator(BaseModel, ABC):
    """One step in an agent run. The class name and set fields are what gets stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: ClassVar[str]

    def to_ref(self) -> OperatorRef:
        """Return the serialized operator an executor can rebuild."""
        raw: object = self.model_dump(mode="json", include=set(self.model_fields_set))
        if not isinstance(raw, dict):
            raise AgentDefinitionError("operator fields must be an object")
        params: dict[str, JsonValue] = {}
        key: object
        value: object
        for key, value in raw.items():
            params[str(key)] = cast(JsonValue, value)
        return OperatorRef(type=type(self).__name__, params=params)

    def context_flags(self) -> dict[str, bool]:
        """Build flags this step contributes. Most steps contribute none."""
        return {}

    @abstractmethod
    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Run this step."""


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
