"""Write operator markdown and JSON into the asset object store."""

import json
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy.orm import Session

from intelegent_data_catalog.core.settings import AssetDefinitionsSettings
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import AssetOutputRow
from intelegent_data_catalog.storage.object_store import ObjectStore


class AssetOutputStore:
    """Stores one step's markdown and optional JSON, and indexes the run copy."""

    def __init__(
        self,
        store: ObjectStore,
        session: Session,
        settings: AssetDefinitionsSettings,
        asset_id: UUID,
        asset_key: str,
        run_id: UUID,
    ) -> None:
        self._store: ObjectStore = store
        self._session: Session = session
        self._settings: AssetDefinitionsSettings = settings
        self._asset_id: UUID = asset_id
        self._asset_key: str = asset_key
        self._run_id: UUID = run_id

    def write(self, task_id: str, name: str, markdown: str, data: JsonValue | None) -> None:
        """Put the run copy and the latest copy, then insert one output row."""
        if task_id == "":
            raise CatalogError("output has no task")
        prefix: str = self._settings.outputs_prefix
        folder: str = f"{prefix}{self._asset_key}/{task_id}"
        markdown_key: str = f"{folder}/{self._run_id}.md"
        body: bytes = markdown.encode("utf-8")
        self._store.put(markdown_key, body, "text/markdown; charset=utf-8")
        self._store.put(f"{folder}/latest.md", body, "text/markdown; charset=utf-8")
        json_key: str | None = None
        if data is not None:
            encoded: bytes = json.dumps(data, indent=2, sort_keys=True, default=str).encode("utf-8")
            json_key = f"{folder}/{self._run_id}.json"
            self._store.put(json_key, encoded, "application/json")
            self._store.put(f"{folder}/latest.json", encoded, "application/json")
        self._session.add(
            AssetOutputRow(
                id=uuid4(),
                asset_id=self._asset_id,
                run_id=self._run_id,
                task_id=task_id,
                name=name,
                markdown_key=markdown_key,
                json_key=json_key,
                size_bytes=len(body),
                created_at=utc_now(),
            ),
        )
        self._session.flush()
