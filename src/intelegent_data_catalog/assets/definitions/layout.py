"""Map an object key to the asset definition folder."""

from intelegent_data_catalog.core.settings import AssetDefinitionsSettings
from intelegent_data_catalog.persistence.enums import DefinitionKind

NAME_PREFIX: str = "asset_"


class DefinitionLocation:
    """Where one object key sits in the definition tree."""

    def __init__(self, filename: str, *, name_ok: bool) -> None:
        self.kind: DefinitionKind = DefinitionKind.ASSET
        self.filename: str = filename
        self.name_ok: bool = name_ok
        self.name_prefix: str = NAME_PREFIX


class DefinitionLayout:
    """Classify keys under the prefix. Unknown folders and non-Python files are ignored."""

    def __init__(self, settings: AssetDefinitionsSettings) -> None:
        prefix: str = settings.prefix
        self._prefix: str = prefix if prefix.endswith("/") or prefix == "" else f"{prefix}/"
        self._folder: str = settings.assets_folder

    def locate(self, key: str) -> DefinitionLocation | None:
        """Return a location for ``key``, or none when the scanner should ignore it."""
        if not key.startswith(self._prefix):
            return None
        relative: str = key[len(self._prefix) :]
        if relative == "" or relative.endswith("/"):
            return None
        segment: str
        _rest: str
        segment, _separator, _rest = relative.partition("/")
        if segment != self._folder:
            return None
        filename: str = relative.rsplit("/", 1)[-1]
        if not filename.endswith(".py"):
            return None
        return DefinitionLocation(filename, name_ok=filename.startswith(NAME_PREFIX))
