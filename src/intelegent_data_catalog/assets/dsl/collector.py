"""Collects declarations while a definition file executes."""

from contextvars import ContextVar, Token

from intelegent_data_catalog.assets.definitions.models import (
    AssetSpec,
    DefinitionSpec,
    ScopeSpec,
    SpaceSpec,
)
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.persistence.enums import DefinitionKind


class DefinitionCollector:
    """Declarations from the file that is executing."""

    def __init__(self, expected: DefinitionKind) -> None:
        self.expected: DefinitionKind = expected
        self.spaces: list[SpaceSpec] = []
        self.scopes: list[ScopeSpec] = []
        self.assets: list[AssetSpec] = []
        self._keys: set[str] = set()

    def add_space(self, spec: SpaceSpec) -> None:
        """Record one space. The file must be a spaces file."""
        self._expect(DefinitionKind.SPACE)
        self._unique(spec.slug)
        self.spaces.append(spec)

    def add_scope(self, spec: ScopeSpec) -> None:
        """Record one scope. The file must be a scopes file."""
        self._expect(DefinitionKind.SCOPE)
        self._unique(f"{spec.space_slug}/{spec.slug}")
        self.scopes.append(spec)

    def add_asset(self, spec: AssetSpec) -> None:
        """Record one asset. The file must be an assets file."""
        self._expect(DefinitionKind.ASSET)
        self._unique(spec.key)
        self.assets.append(spec)

    def spec(self) -> DefinitionSpec:
        """Return the declarations collected so far."""
        return DefinitionSpec(
            spaces=list(self.spaces),
            scopes=list(self.scopes),
            assets=list(self.assets),
        )

    def _expect(self, kind: DefinitionKind) -> None:
        if kind != self.expected:
            raise AssetDefinitionError(
                f"{self.expected.value} file cannot declare {kind.value}",
            )

    def _unique(self, key: str) -> None:
        if key in self._keys:
            raise AssetDefinitionError(f"duplicate key: {key}")
        self._keys.add(key)


_CURRENT: ContextVar[DefinitionCollector | None] = ContextVar(
    "asset_definition_collector",
    default=None,
)


def current_collector() -> DefinitionCollector:
    """Return the collector for the file that is executing."""
    collector: DefinitionCollector | None = _CURRENT.get()
    if collector is None:
        raise AssetDefinitionError("definition object must be created while a file is collected")
    return collector


def bind_collector(collector: DefinitionCollector) -> Token[DefinitionCollector | None]:
    """Install ``collector`` for the current thread."""
    return _CURRENT.set(collector)


def reset_collector(token: Token[DefinitionCollector | None]) -> None:
    """Restore the previous collector."""
    _CURRENT.reset(token)
