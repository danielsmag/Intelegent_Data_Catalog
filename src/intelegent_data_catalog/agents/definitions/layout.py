"""Map an object key to the definition kind its folder allows."""

from intelegent_data_catalog.core.settings import AgentDefinitionsSettings
from intelegent_data_catalog.persistence.enums import DefinitionKind

KIND_RANK: dict[str, int] = {
    DefinitionKind.AGENT.value: 0,
}
NAME_PREFIX: dict[DefinitionKind, str] = {
    DefinitionKind.AGENT: "agent_",
}


class DefinitionLocation:
    """Where one object key sits in the definition tree."""

    def __init__(
        self,
        kind: DefinitionKind,
        filename: str,
        *,
        name_ok: bool,
        name_prefix: str,
    ) -> None:
        self.kind: DefinitionKind = kind
        self.filename: str = filename
        self.name_ok: bool = name_ok
        self.name_prefix: str = name_prefix


class DefinitionLayout:
    """Classify keys under the prefix. Unknown folders and non-Python files are ignored."""

    def __init__(self, settings: AgentDefinitionsSettings) -> None:
        prefix: str = settings.prefix
        self._prefix: str = prefix if prefix.endswith("/") or prefix == "" else f"{prefix}/"
        self._folders: dict[str, DefinitionKind] = {
            settings.agents_folder: DefinitionKind.AGENT,
        }

    def locate(self, key: str) -> DefinitionLocation | None:
        """Return the kind for ``key``, or none when the scanner should ignore it."""
        if not key.startswith(self._prefix):
            return None
        relative: str = key[len(self._prefix) :]
        if relative == "" or relative.endswith("/"):
            return None
        segment: str
        _rest: str
        segment, _separator, _rest = relative.partition("/")
        kind: DefinitionKind | None = self._folders.get(segment)
        if kind is None:
            return None
        filename: str = relative.rsplit("/", 1)[-1]
        if not filename.endswith(".py"):
            return None
        name_prefix: str = NAME_PREFIX[kind]
        return DefinitionLocation(
            kind,
            filename,
            name_ok=filename.startswith(name_prefix),
            name_prefix=name_prefix,
        )
