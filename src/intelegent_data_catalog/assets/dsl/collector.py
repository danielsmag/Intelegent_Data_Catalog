"""Collects declarations while an asset definition file executes."""

from contextvars import ContextVar, Token

from intelegent_data_catalog.assets.definitions.models import AssetSpec, DefinitionSpec
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.persistence.enums import DefinitionKind


class DefinitionCollector:
    """Declarations from the file that is executing."""

    def __init__(self, expected: DefinitionKind) -> None:
        self.expected: DefinitionKind = expected
        self.assets: list[AssetSpec] = []
        self._keys: set[str] = set()

    def add_asset(self, spec: AssetSpec) -> None:
        """Record one asset. The file must be an assets file."""
        if self.expected is not DefinitionKind.ASSET:
            raise AssetDefinitionError(
                f"{self.expected.value} file cannot declare {DefinitionKind.ASSET.value}",
            )
        if spec.key in self._keys:
            raise AssetDefinitionError(f"duplicate key: {spec.key}")
        self._keys.add(spec.key)
        self.assets.append(spec)

    def spec(self) -> DefinitionSpec:
        """Return the declarations collected so far."""
        return DefinitionSpec(assets=list(self.assets))


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
