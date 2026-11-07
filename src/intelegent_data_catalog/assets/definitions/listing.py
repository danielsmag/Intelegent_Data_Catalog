"""List asset definition objects that exist in the object store."""

from intelegent_data_catalog.assets.definitions.models import (
    AssetDefinitionObjectRead,
    DefinitionFolder,
)
from intelegent_data_catalog.core.settings import AssetDefinitionsSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.storage.object_store import ObjectStore, StoredObject


class AssetDefinitionListing:
    """Reads keys under the assets folder. The scan tables are not consulted."""

    def __init__(self, store: ObjectStore, settings: AssetDefinitionsSettings) -> None:
        self._store: ObjectStore = store
        self._settings: AssetDefinitionsSettings = settings

    def list_folder(self, folder: DefinitionFolder) -> list[AssetDefinitionObjectRead]:
        """Return every file under ``folder``. Directory markers are omitted."""
        prefix: str = f"{self._settings.prefix}{self._segment(folder)}/"
        found: list[AssetDefinitionObjectRead] = []
        stored: StoredObject
        for stored in self._store.list(prefix):
            relative: str = stored.key[len(prefix) :]
            if relative == "" or relative.endswith("/"):
                continue
            found.append(
                AssetDefinitionObjectRead(
                    key=stored.key,
                    path=relative,
                    size_bytes=stored.size_bytes,
                    last_modified=stored.last_modified,
                ),
            )
        return found

    def read_text(self, key: str) -> str:
        """Return the UTF-8 script stored at ``key``."""
        if not self._allowed(key):
            raise EntityNotFoundError("object", key)
        raw: bytes = self._store.get(key)
        if len(raw) > self._settings.max_file_bytes:
            raise EntityNotFoundError("object", key)
        return raw.decode("utf-8", errors="replace")

    def _allowed(self, key: str) -> bool:
        prefix: str = self._settings.prefix
        if not key.startswith(prefix):
            return False
        relative: str = key[len(prefix) :]
        if relative == "" or relative.endswith("/") or ".." in relative.split("/"):
            return False
        segment: str
        rest: str
        segment, _separator, rest = relative.partition("/")
        return segment == self._settings.assets_folder and rest != ""

    def _segment(self, folder: DefinitionFolder) -> str:
        del folder
        return self._settings.assets_folder
