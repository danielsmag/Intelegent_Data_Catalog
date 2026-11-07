"""Collects declarations while a definition file executes."""

from contextvars import ContextVar, Token

from intelegent_data_catalog.agents.definitions.models import (
    AgentSpec,
    DefinitionSpec,
)
from intelegent_data_catalog.agents.dsl.errors import AgentDefinitionError
from intelegent_data_catalog.persistence.enums import DefinitionKind


class DefinitionCollector:
    """Declarations from the file that is executing."""

    def __init__(self, expected: DefinitionKind) -> None:
        self.expected: DefinitionKind = expected
        self.agents: list[AgentSpec] = []
        self._keys: set[str] = set()

    def add_agent(self, spec: AgentSpec) -> None:
        """Record one agent. The file must be an agents file."""
        self._expect(DefinitionKind.AGENT)
        self._unique(spec.key)
        self.agents.append(spec)

    def spec(self) -> DefinitionSpec:
        """Return the declarations collected so far."""
        return DefinitionSpec(agents=list(self.agents))

    def _expect(self, kind: DefinitionKind) -> None:
        if kind != self.expected:
            raise AgentDefinitionError(
                f"{self.expected.value} file cannot declare {kind.value}",
            )

    def _unique(self, key: str) -> None:
        if key in self._keys:
            raise AgentDefinitionError(f"duplicate key: {key}")
        self._keys.add(key)


_CURRENT: ContextVar[DefinitionCollector | None] = ContextVar(
    "agent_definition_collector",
    default=None,
)


def current_collector() -> DefinitionCollector:
    """Return the collector for the file that is executing."""
    collector: DefinitionCollector | None = _CURRENT.get()
    if collector is None:
        raise AgentDefinitionError("definition object must be created while a file is collected")
    return collector


def bind_collector(collector: DefinitionCollector) -> Token[DefinitionCollector | None]:
    """Install ``collector`` for the current thread."""
    return _CURRENT.set(collector)


def reset_collector(token: Token[DefinitionCollector | None]) -> None:
    """Restore the previous collector."""
    _CURRENT.reset(token)
