"""List definition objects that exist in the object store."""

from intelegent_data_catalog.agents.definitions.models import (
    AgentDefinitionObjectRead,
    DefinitionFolder,
)
from intelegent_data_catalog.core.settings import AgentDefinitionsSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.storage.object_store import ObjectStore, StoredObject


class AgentDefinitionListing:
    """Reads keys under one definition folder. The scan tables are not consulted."""

    def __init__(self, store: ObjectStore, settings: AgentDefinitionsSettings) -> None:
        self._store: ObjectStore = store
        self._settings: AgentDefinitionsSettings = settings

    def list_folder(self, folder: DefinitionFolder) -> list[AgentDefinitionObjectRead]:
        """Return every file under ``folder``. Directory markers are omitted."""
        prefix: str = f"{self._settings.prefix}{self._segment(folder)}/"
        found: list[AgentDefinitionObjectRead] = []
        stored: StoredObject
        for stored in self._store.list(prefix):
            relative: str = stored.key[len(prefix) :]
            if relative == "" or relative.endswith("/"):
                continue
            found.append(
                AgentDefinitionObjectRead(
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
        folders: set[str] = {self._settings.agents_folder}
        return segment in folders and rest != ""

    def _segment(self, folder: DefinitionFolder) -> str:
        del folder
        return self._settings.agents_folder
