"""Apply asset declarations and mark assets no file still names."""

import logging
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.definitions.fingerprint import declaration_payload, fingerprint
from intelegent_data_catalog.assets.definitions.models import (
    AssetSpec,
    DefinitionSpec,
    SourceRef,
    asset_kind_for,
)
from intelegent_data_catalog.assets.definitions.repository import AssetDefinitionRepository
from intelegent_data_catalog.assets.definitions.resolver import SourceResolver, UnresolvedSource
from intelegent_data_catalog.assets.dsl.operators.ports import GitHubReader, IcebergReader
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    AssetStatus,
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
)
from intelegent_data_catalog.persistence.models import (
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
    AssetRow,
)

_MAX_ERROR: int = 8000


class AssetDefinitionApplier:
    """Writes asset rows for one file. The scanner commits."""

    def __init__(
        self,
        session: Session,
        logger: logging.Logger,
        iceberg: IcebergReader,
        github: GitHubReader,
    ) -> None:
        self._session: Session = session
        self._logger: logging.Logger = logger
        self._repo: AssetDefinitionRepository = AssetDefinitionRepository(session)
        self._resolver: SourceResolver = SourceResolver(session, iceberg, github)
        self._pending: list[OperatorWorkload] = []

    def take_workloads(self) -> list[OperatorWorkload]:
        """Return operator runs collected since the last take, then clear them."""
        found: list[OperatorWorkload] = list(self._pending)
        self._pending.clear()
        return found

    def drop_workloads(self) -> None:
        """Forget operator runs collected for a file that failed."""
        self._pending.clear()

    def apply_file(self, file_row: AssetDefinitionFileRow, spec: DefinitionSpec) -> None:
        """Upsert declarations and retire ones the file no longer contains."""
        existing: dict[str, AssetDefinitionItemRow] = {
            item.item_key: item for item in self._repo.items_for(file_row.id)
        }
        declared_keys: set[str] = {asset.key for asset in spec.assets}
        key: str
        prior: AssetDefinitionItemRow
        for key, prior in existing.items():
            if key not in declared_keys:
                self._retire_item(prior)
        asset: AssetSpec
        for asset in spec.assets:
            prior_item: AssetDefinitionItemRow | None = existing.get(asset.key)
            if _unchanged(prior_item, asset):
                continue
            self._apply_asset(file_row, asset, prior_item)

    def retire_file(self, file_row: AssetDefinitionFileRow) -> None:
        """Delete the items this file created and mark orphaned assets missing."""
        item: AssetDefinitionItemRow
        for item in self._repo.items_for(file_row.id):
            self._retire_item(item)

    def retry_unresolved(self) -> int:
        """Re-apply unresolved items from their stored declaration."""
        applied: int = 0
        item: AssetDefinitionItemRow
        for item in self._repo.unresolved():
            item_id: UUID = item.id
            kept: int = len(self._pending)
            try:
                status: DefinitionItemStatus = self._retry(item)
                self._session.commit()
            except (CatalogError, ValidationError, SQLAlchemyError):
                self._logger.exception("asset definition retry failed: %s", item_id)
                self._pending = self._pending[:kept]
                self._session.rollback()
                self._mark_retry_error(item_id)
                self._session.commit()
                continue
            if status == DefinitionItemStatus.APPLIED:
                applied += 1
        return applied

    def mark_missing(self) -> int:
        """Mark assets no applied declaration still names."""
        declared: set[UUID] = self._repo.applied_asset_ids()
        marked: int = 0
        asset: AssetRow
        for asset in list(self._session.scalars(select(AssetRow))):
            if asset.id in declared or asset.status == AssetStatus.MISSING.value:
                continue
            asset.status = AssetStatus.MISSING.value
            asset.updated_at = utc_now()
            marked += 1
        return marked

    def _retry(self, item: AssetDefinitionItemRow) -> DefinitionItemStatus:
        file_row: AssetDefinitionFileRow | None = self._session.get(
            AssetDefinitionFileRow,
            item.file_id,
        )
        if file_row is None or file_row.status == DefinitionFileStatus.REMOVED.value:
            return DefinitionItemStatus.UNRESOLVED
        return self._apply_asset(file_row, AssetSpec.model_validate(item.declaration), item)

    def _apply_asset(
        self,
        file_row: AssetDefinitionFileRow,
        spec: AssetSpec,
        prior: AssetDefinitionItemRow | None,
    ) -> DefinitionItemStatus:
        resolved: UnresolvedSource | None = self._resolver.resolve(spec.source)
        if isinstance(resolved, UnresolvedSource):
            self._record(
                file_row,
                spec,
                DefinitionItemStatus.UNRESOLVED,
                resolved.reason,
                prior,
                asset_id=None,
            )
            return DefinitionItemStatus.UNRESOLVED
        asset: AssetRow = self._ensure_asset(spec, prior, file_row.key)
        recorded: AssetDefinitionItemRow
        changed: bool
        recorded, changed = self._record(
            file_row,
            spec,
            DefinitionItemStatus.APPLIED,
            None,
            prior,
            asset_id=asset.id,
        )
        if changed and len(spec.operators) > 0:
            self._queue(file_row, recorded, asset, spec)
        return DefinitionItemStatus.APPLIED

    def _ensure_asset(
        self,
        spec: AssetSpec,
        prior: AssetDefinitionItemRow | None,
        file_key: str,
    ) -> AssetRow:
        existing: AssetRow | None = None
        if prior is not None and prior.asset_id is not None:
            existing = self._session.get(AssetRow, prior.asset_id)
        if existing is None:
            existing = self._repo.asset_by_key(spec.key)
        if existing is not None and existing.key != spec.key:
            existing = self._repo.asset_by_key(spec.key)
        if existing is not None and _claimed_elsewhere(self._repo, existing, prior):
            raise CatalogError(f"asset key already declared: {spec.key}")
        now: datetime = utc_now()
        if existing is None:
            created: AssetRow = AssetRow(
                id=uuid4(),
                kind=asset_kind_for(spec.source).value,
                key=spec.key,
                name=spec.key,
                description=spec.description,
                source=_source_payload(spec.source),
                properties=_properties({}, spec, file_key),
                status=AssetStatus.ACTIVE.value,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
            self._session.flush()
            return created
        existing.kind = asset_kind_for(spec.source).value
        existing.key = spec.key
        existing.name = spec.key
        if "description" in spec.set_fields:
            existing.description = spec.description
        existing.source = _source_payload(spec.source)
        existing.properties = _properties(dict(existing.properties), spec, file_key)
        existing.status = AssetStatus.ACTIVE.value
        existing.updated_at = now
        self._session.flush()
        return existing

    def _queue(
        self,
        file_row: AssetDefinitionFileRow,
        item: AssetDefinitionItemRow,
        asset: AssetRow,
        spec: AssetSpec,
    ) -> None:
        item.run_status = DefinitionRunStatus.QUEUED.value
        item.run_error = None
        item.schedule = spec.schedule
        item.next_run_at = None
        item.updated_at = utc_now()
        self._session.flush()
        self._pending.append(
            OperatorWorkload(
                item_id=item.id,
                asset_id=asset.id,
                asset_key=asset.key,
                file_key=file_row.key,
                item_key=spec.key,
                operators=list(spec.operators),
                force=True,
            ),
        )

    def _retire_item(self, item: AssetDefinitionItemRow) -> None:
        asset_id: UUID | None = item.asset_id
        self._repo.delete_item(item)
        if asset_id is None or self._repo.other_applied_claim(asset_id, item.id):
            return
        asset: AssetRow | None = self._session.get(AssetRow, asset_id)
        if asset is None:
            return
        asset.status = AssetStatus.MISSING.value
        asset.updated_at = utc_now()

    def _record(
        self,
        file_row: AssetDefinitionFileRow,
        spec: AssetSpec,
        status: DefinitionItemStatus,
        error: str | None,
        prior: AssetDefinitionItemRow | None,
        *,
        asset_id: UUID | None,
    ) -> tuple[AssetDefinitionItemRow, bool]:
        digest: str = fingerprint(spec)
        payload: dict[str, object] = declaration_payload(spec)
        message: str | None = _clip_error(error)
        if prior is not None and _same_record(prior, status, digest, message, asset_id):
            return prior, False
        now: datetime = utc_now()
        if prior is None:
            created: AssetDefinitionItemRow = AssetDefinitionItemRow(
                id=uuid4(),
                file_id=file_row.id,
                kind=DefinitionKind.ASSET.value,
                item_key=spec.key,
                declaration=payload,
                fingerprint=digest,
                asset_id=asset_id,
                status=status.value,
                error=message,
                run_status=None,
                run_error=None,
                ran_at=None,
                schedule=spec.schedule,
                next_run_at=None,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
            self._session.flush()
            return created, True
        prior.kind = DefinitionKind.ASSET.value
        prior.item_key = spec.key
        prior.declaration = payload
        prior.fingerprint = digest
        prior.asset_id = asset_id
        prior.status = status.value
        prior.error = message
        prior.schedule = spec.schedule
        prior.updated_at = now
        self._session.flush()
        return prior, True

    def _mark_retry_error(self, item_id: UUID) -> None:
        row: AssetDefinitionItemRow | None = self._session.get(AssetDefinitionItemRow, item_id)
        if row is None:
            return
        row.status = DefinitionItemStatus.ERROR.value
        row.error = _clip_error("retry failed")
        row.updated_at = utc_now()
        self._session.flush()


def _claimed_elsewhere(
    repo: AssetDefinitionRepository,
    asset: AssetRow,
    prior: AssetDefinitionItemRow | None,
) -> bool:
    item_id: UUID = prior.id if prior is not None else uuid4()
    if prior is None:
        return repo.other_applied_claim(asset.id, item_id)
    return repo.other_applied_claim(asset.id, prior.id)


def _properties(current: dict[str, object], spec: AssetSpec, file_key: str) -> dict[str, object]:
    properties: dict[str, object] = dict(current)
    properties["definition"] = {"file": file_key, "key": spec.key}
    if "tags" in spec.set_fields:
        properties["tags"] = list(spec.tags)
    return properties


def _source_payload(source: SourceRef) -> dict[str, object]:
    dumped: dict[str, object] = {}
    key: str
    value: object
    for key, value in source.model_dump(mode="json").items():
        dumped[key] = value
    return dumped


def _unchanged(prior: AssetDefinitionItemRow | None, model: BaseModel) -> bool:
    if prior is None:
        return False
    applied: bool = prior.status == DefinitionItemStatus.APPLIED.value
    return applied and prior.fingerprint == fingerprint(model)


def _same_record(
    prior: AssetDefinitionItemRow,
    status: DefinitionItemStatus,
    digest: str,
    message: str | None,
    asset_id: UUID | None,
) -> bool:
    return (
        prior.status == status.value
        and prior.fingerprint == digest
        and prior.error == message
        and prior.asset_id == asset_id
    )


def _clip_error(message: str | None) -> str | None:
    if message is None or len(message) <= _MAX_ERROR:
        return message
    return message[:_MAX_ERROR]
