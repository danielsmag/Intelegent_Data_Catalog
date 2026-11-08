"""Asset reads and manual runs."""

import json
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.models import AssetPage, AssetRead, AssetSpec
from intelegent_data_catalog.assets.execution.executor import OperatorExecutor
from intelegent_data_catalog.assets.execution.models import (
    AssetOutputDetail,
    AssetOutputRead,
    AssetRunRead,
    AssetRunTaskDetail,
)
from intelegent_data_catalog.assets.execution.runs import AssetRunReader
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.assets.profiling.models import (
    PROFILE_TASK_IDS,
    AssetProfileRead,
    ProfileDocument,
)
from intelegent_data_catalog.assets.repository import AssetRepository
from intelegent_data_catalog.exceptions import (
    ConflictError,
    EntityNotFoundError,
    GraphUnavailableError,
    ValidationAppError,
)
from intelegent_data_catalog.graph.document import GraphView
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    AssetKind,
    AssetStatus,
    DefinitionRunStatus,
    OperatorRunTrigger,
)
from intelegent_data_catalog.persistence.models import (
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
    AssetOutputRow,
    AssetRow,
)
from intelegent_data_catalog.storage.object_store import ObjectStore

_BUSY: frozenset[str] = frozenset(
    {DefinitionRunStatus.QUEUED.value, DefinitionRunStatus.RUNNING.value},
)


class ProfileGraphReader(Protocol):
    """Reads the Neo4j profile subgraph for one asset."""

    def read_profile(self, owner_asset_id: str) -> GraphView:
        """Return the subgraph owned by ``owner_asset_id``."""


class AssetService:
    """Lists assets, their runs and markdown, and queues a manual run."""

    def __init__(
        self,
        session: Session,
        store: ObjectStore,
        executor: OperatorExecutor,
        graphs: ProfileGraphReader | None,
    ) -> None:
        self._session: Session = session
        self._store: ObjectStore = store
        self._executor: OperatorExecutor = executor
        self._graphs: ProfileGraphReader | None = graphs
        self._assets: AssetRepository = AssetRepository(session)
        self._runs: AssetRunReader = AssetRunReader(session)

    def search(
        self,
        *,
        text: str | None,
        kind: AssetKind | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> AssetPage:
        """Return one page of assets."""
        rows: list[AssetRow]
        total: int
        rows, total = self._assets.search(
            text=text,
            kind=kind,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        items: dict[UUID, AssetDefinitionItemRow] = self._assets.schedules([row.id for row in rows])
        return AssetPage(
            items=[_read(row, items.get(row.id)) for row in rows],
            total=total,
        )

    def get(self, asset_id: UUID) -> AssetRead:
        """Return one asset."""
        row: AssetRow = self._require(asset_id)
        return _read(row, self._assets.applied_item(asset_id))

    def runs(self, asset_id: UUID, limit: int) -> list[AssetRunRead]:
        """Return recent operator runs."""
        self._require(asset_id)
        return self._runs.list_for_asset(asset_id, limit)

    def run_task(self, asset_id: UUID, run_id: UUID, task_id: str) -> AssetRunTaskDetail:
        """Return one task log."""
        self._require(asset_id)
        return self._runs.get_task(asset_id, run_id, task_id)

    def outputs(self, asset_id: UUID) -> list[AssetOutputRead]:
        """Return stored markdown results, newest first."""
        self._require(asset_id)
        return [_output(row) for row in self._assets.outputs(asset_id)]

    def profile(self, asset_id: UUID) -> AssetProfileRead:
        """Return the newest profile document and its markdown."""
        self._require(asset_id)
        row: AssetOutputRow | None = self._assets.latest_output(asset_id, PROFILE_TASK_IDS)
        if row is None or row.json_key is None:
            raise EntityNotFoundError("asset profile", str(asset_id))
        markdown: str = self._store.get(row.markdown_key).decode("utf-8", errors="replace")
        raw: object = json.loads(self._store.get(row.json_key).decode("utf-8"))
        try:
            document: ProfileDocument = ProfileDocument.model_validate(raw)
        except ValidationError as err:
            raise ValidationAppError("asset profile document is invalid") from err
        return AssetProfileRead(
            output_id=row.id,
            asset_id=row.asset_id,
            run_id=row.run_id,
            created_at=row.created_at,
            document=document,
            markdown=markdown,
        )

    def profile_graph(self, asset_id: UUID) -> GraphView:
        """Return the Neo4j profile subgraph for one asset."""
        self._require(asset_id)
        if self._graphs is None:
            raise GraphUnavailableError("graph projection is not configured")
        return self._graphs.read_profile(str(asset_id))

    def output(self, asset_id: UUID, output_id: UUID) -> AssetOutputDetail:
        """Return one markdown document and its JSON sidecar."""
        self._require(asset_id)
        row: AssetOutputRow | None = self._assets.output(asset_id, output_id)
        if row is None:
            raise EntityNotFoundError("asset output", str(output_id))
        markdown: str = self._store.get(row.markdown_key).decode("utf-8", errors="replace")
        data: JsonValue | None = None
        if row.json_key is not None:
            raw: object = json.loads(self._store.get(row.json_key).decode("utf-8"))
            if isinstance(raw, (dict, list, str, int, float, bool)) or raw is None:
                data = raw
        return AssetOutputDetail(
            id=row.id,
            asset_id=row.asset_id,
            run_id=row.run_id,
            task_id=row.task_id,
            name=row.name,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
            markdown=markdown,
            data=data,
        )

    def trigger(self, asset_id: UUID, *, force: bool) -> None:
        """Queue the asset's operators. A run already in progress is rejected."""
        asset: AssetRow = self._require(asset_id)
        item: AssetDefinitionItemRow | None = self._assets.applied_item(asset_id)
        if item is None:
            raise ValidationAppError("asset has no applied declaration")
        if item.run_status in _BUSY:
            raise ConflictError("asset run is already in progress")
        try:
            spec: AssetSpec = AssetSpec.model_validate(item.declaration)
        except ValidationError as err:
            raise ValidationAppError("asset declaration is invalid") from err
        if len(spec.operators) == 0:
            raise ValidationAppError("asset has no operators")
        file_row: AssetDefinitionFileRow | None = self._session.get(
            AssetDefinitionFileRow,
            item.file_id,
        )
        if file_row is None:
            raise ValidationAppError("asset definition file is missing")
        item.run_status = DefinitionRunStatus.QUEUED.value
        item.run_error = None
        item.updated_at = utc_now()
        self._session.commit()
        workload: OperatorWorkload = OperatorWorkload(
            item_id=item.id,
            asset_id=asset.id,
            asset_key=asset.key,
            file_key=file_row.key,
            item_key=item.item_key,
            operators=list(spec.operators),
            trigger=OperatorRunTrigger.MANUAL,
            force=force,
        )
        try:
            self._executor.submit(workload)
        except Exception:
            item.run_status = DefinitionRunStatus.FAILED.value
            item.run_error = "operator submit failed"
            item.updated_at = utc_now()
            self._session.commit()
            raise

    def _require(self, asset_id: UUID) -> AssetRow:
        row: AssetRow | None = self._assets.get(asset_id)
        if row is None:
            raise EntityNotFoundError("asset", str(asset_id))
        return row


def _read(row: AssetRow, item: AssetDefinitionItemRow | None) -> AssetRead:
    return AssetRead(
        id=row.id,
        kind=AssetKind(row.kind),
        key=row.key,
        name=row.name,
        description=row.description,
        status=AssetStatus(row.status),
        source=_json_object(row.source),
        properties=_json_object(row.properties),
        schedule=None if item is None else item.schedule,
        next_run_at=None if item is None else item.next_run_at,
        run_status=_run_status(item),
        ran_at=None if item is None else item.ran_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_status(item: AssetDefinitionItemRow | None) -> DefinitionRunStatus | None:
    if item is None or item.run_status is None:
        return None
    return DefinitionRunStatus(item.run_status)


def _output(row: AssetOutputRow) -> AssetOutputRead:
    return AssetOutputRead(
        id=row.id,
        asset_id=row.asset_id,
        run_id=row.run_id,
        task_id=row.task_id,
        name=row.name,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


def _json_object(raw: object) -> dict[str, JsonValue]:
    if not isinstance(raw, dict):
        return {}
    found: dict[str, JsonValue] = {}
    key: object
    value: object
    for key, value in raw.items():
        found[str(key)] = _json_value(value)
    return found


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        nested: dict[str, JsonValue] = {}
        key: object
        raw: object
        for key, raw in value.items():
            nested[str(key)] = _json_value(raw)
        return nested
    return str(value)
