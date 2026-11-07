"""Apply asset declarations and drop assets no file still names."""

import logging
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.fingerprint import declaration_payload, fingerprint
from intelegent_data_catalog.assets.definitions.models import (
    AssetSpec,
    DefinitionSpec,
    SchemaSourceRef,
    TableSourceRef,
)
from intelegent_data_catalog.assets.definitions.repository import AssetDefinitionRepository
from intelegent_data_catalog.assets.definitions.resolver import (
    RejectedSource,
    ResolvedSource,
    SourceResolver,
    UnresolvedSource,
)
from intelegent_data_catalog.assets.execution.workload import OperatorWorkload
from intelegent_data_catalog.assets.repository import AssetRepository
from intelegent_data_catalog.assets.sync import AssetSync
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
)
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AirflowInstanceRow,
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
    AssetRow,
    CatalogTableRow,
    WorkspaceNodeRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.services.support import apply_user_context

_MAX_ERROR: int = 8000


class AssetDefinitionApplier:
    """Writes catalog rows for one file. The scanner commits. Retries commit per item."""

    def __init__(self, session: Session, logger: logging.Logger) -> None:
        self._session: Session = session
        self._logger: logging.Logger = logger
        self._repo: AssetDefinitionRepository = AssetDefinitionRepository(session)
        self._registry: RegistryRepository = RegistryRepository(session)
        self._assets: AssetRepository = AssetRepository(session)
        self._resolver: SourceResolver = SourceResolver(session)
        self._sync: AssetSync = AssetSync(session)
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
        declared: list[tuple[str, DefinitionKind, BaseModel]] = _declared(spec)
        declared_keys: set[str] = {item[0] for item in declared}
        schema_keys: set[str] = {
            asset.key for asset in spec.assets if isinstance(asset.source, SchemaSourceRef)
        }
        for key, prior in existing.items():
            if key in declared_keys or key.split("/", 1)[0] in schema_keys:
                continue
            self._retire_item(prior)
        for item_key, kind, model in declared:
            prior_item: AssetDefinitionItemRow | None = existing.get(item_key)
            schema_asset: bool = isinstance(model, AssetSpec) and isinstance(
                model.source,
                SchemaSourceRef,
            )
            if not schema_asset and _unchanged(prior_item, model):
                continue
            self._apply_model(file_row, kind, model, prior_item)

    def retire_file(self, file_row: AssetDefinitionFileRow) -> None:
        """Delete the items this file created."""
        item: AssetDefinitionItemRow
        for item in self._repo.items_for(file_row.id):
            self._retire_item(item)

    def retry_unresolved(self) -> int:
        """Re-apply unresolved items from their stored declaration. Each item commits."""
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

    def refresh_schemas(self) -> int:
        """Re-resolve applied schema declarations so new and gone tables are picked up."""
        refreshed: int = 0
        item: AssetDefinitionItemRow
        for item in self._repo.schema_parents():
            item_id: UUID = item.id
            kept: int = len(self._pending)
            try:
                self._refresh_schema(item)
                self._session.commit()
            except (CatalogError, ValidationError, SQLAlchemyError):
                self._logger.exception("asset schema refresh failed: %s", item_id)
                self._pending = self._pending[:kept]
                self._session.rollback()
                self._mark_retry_error(item_id)
                self._session.commit()
                continue
            refreshed += 1
        return refreshed

    def _refresh_schema(self, item: AssetDefinitionItemRow) -> None:
        file_row: AssetDefinitionFileRow | None = self._session.get(
            AssetDefinitionFileRow,
            item.file_id,
        )
        if file_row is None or file_row.status == DefinitionFileStatus.REMOVED.value:
            return
        spec: AssetSpec = AssetSpec.model_validate(item.declaration)
        self._apply_schema(file_row, spec, item)

    def _retry(self, item: AssetDefinitionItemRow) -> DefinitionItemStatus:
        file_row: AssetDefinitionFileRow | None = self._session.get(
            AssetDefinitionFileRow,
            item.file_id,
        )
        if file_row is None or file_row.status == DefinitionFileStatus.REMOVED.value:
            return DefinitionItemStatus.UNRESOLVED
        kind: DefinitionKind = DefinitionKind(item.kind)
        if kind is not DefinitionKind.ASSET:
            return DefinitionItemStatus.ERROR
        return self._apply_asset(file_row, AssetSpec.model_validate(item.declaration), item)

    def _apply_model(
        self,
        file_row: AssetDefinitionFileRow,
        kind: DefinitionKind,
        model: BaseModel,
        prior: AssetDefinitionItemRow | None,
    ) -> None:
        if kind is DefinitionKind.ASSET and isinstance(model, AssetSpec):
            self._apply_asset(file_row, model, prior)
            return
        raise CatalogError(f"declaration kind does not match {kind.value}")



    def _apply_asset(
        self,
        file_row: AssetDefinitionFileRow,
        spec: AssetSpec,
        prior: AssetDefinitionItemRow | None,
    ) -> DefinitionItemStatus:
        if isinstance(spec.source, SchemaSourceRef):
            return self._apply_schema(file_row, spec, prior)
        found: ResolvedSource | UnresolvedSource | RejectedSource | list[CatalogTableRow]
        found = self._resolver.resolve(spec.source)
        if isinstance(found, list):
            raise CatalogError("schema declaration was not expanded")
        resolved: ResolvedSource | UnresolvedSource | RejectedSource = found
        if isinstance(resolved, UnresolvedSource):
            if prior is not None:
                self._detach_claim(prior)
            self._record(
                file_row,
                DefinitionKind.ASSET,
                spec.key,
                spec,
                DefinitionItemStatus.UNRESOLVED,
                resolved.reason,
                prior,
            )
            return DefinitionItemStatus.UNRESOLVED
        if isinstance(resolved, RejectedSource):
            if prior is not None:
                self._detach_claim(prior)
            self._record(
                file_row,
                DefinitionKind.ASSET,
                spec.key,
                spec,
                DefinitionItemStatus.ERROR,
                resolved.reason,
                prior,
            )
            return DefinitionItemStatus.ERROR
        asset: AssetRow = self._ensure_asset(resolved, spec, prior)
        self._overlay(asset, spec, file_row.key)
        recorded: AssetDefinitionItemRow
        changed: bool
        recorded, changed = self._record(
            file_row,
            DefinitionKind.ASSET,
            spec.key,
            spec,
            DefinitionItemStatus.APPLIED,
            None,
            prior,
            asset_id=asset.id,
        )
        if changed and spec.operator is not None:
            self._queue(file_row, recorded, asset, spec)
        return DefinitionItemStatus.APPLIED

    def _apply_schema(
        self,
        file_row: AssetDefinitionFileRow,
        spec: AssetSpec,
        prior: AssetDefinitionItemRow | None,
    ) -> DefinitionItemStatus:
        """Record the schema item and one child asset for each matching table."""
        if not isinstance(spec.source, SchemaSourceRef):
            raise CatalogError("schema declaration has no schema source")
        source: SchemaSourceRef = spec.source
        resolved: ResolvedSource | UnresolvedSource | RejectedSource | list[CatalogTableRow]
        resolved = self._resolver.resolve(source)
        prefix: str = f"{spec.key}/"
        children: list[AssetDefinitionItemRow] = [
            item for item in self._repo.items_for(file_row.id) if item.item_key.startswith(prefix)
        ]
        if isinstance(resolved, UnresolvedSource):
            child: AssetDefinitionItemRow
            for child in children:
                self._retire_item(child)
            self._record(
                file_row,
                DefinitionKind.ASSET,
                spec.key,
                spec,
                DefinitionItemStatus.UNRESOLVED,
                resolved.reason,
                prior,
            )
            return DefinitionItemStatus.UNRESOLVED
        if not isinstance(resolved, list):
            raise CatalogError("schema did not resolve to tables")
        self._record(
            file_row,
            DefinitionKind.ASSET,
            spec.key,
            spec,
            DefinitionItemStatus.APPLIED,
            None,
            prior,
        )
        seen: set[str] = set()
        table: CatalogTableRow
        for table in resolved:
            child_key: str = f"{spec.key}/{table.name}"
            seen.add(child_key)
            child_spec: AssetSpec = AssetSpec(
                key=child_key,
                source=TableSourceRef(source_key=table.source_key, data_source=source.data_source),
                operator=spec.operator,
                parent_key=spec.key,
                schedule=spec.schedule,
                description=spec.description,
                context=spec.context,
                status=spec.status,
                properties=dict(spec.properties),
                tags=list(spec.tags),
                set_fields=list(spec.set_fields),
            )
            prior_child: AssetDefinitionItemRow | None = None
            candidate: AssetDefinitionItemRow
            for candidate in children:
                if candidate.item_key == child_key:
                    prior_child = candidate
            self._apply_asset(file_row, child_spec, prior_child)
        for child in children:
            if child.item_key not in seen:
                self._retire_item(child)
        return DefinitionItemStatus.APPLIED

    def _queue(
        self,
        file_row: AssetDefinitionFileRow,
        item: AssetDefinitionItemRow,
        asset: AssetRow,
        spec: AssetSpec,
    ) -> None:
        if spec.operator is None:
            return
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
                asset_name=asset.name,
                file_key=file_row.key,
                item_key=spec.key,
                operator=spec.operator,
            ),
        )

    def _ensure_asset(
        self,
        resolved: ResolvedSource,
        spec: AssetSpec,
        prior: AssetDefinitionItemRow | None,
    ) -> AssetRow:
        match resolved:
            case CatalogTableRow() if spec.parent_key is not None:
                existing: AssetRow | None = None
                if prior is not None and prior.asset_id is not None:
                    existing = self._session.get(AssetRow, prior.asset_id)
                raw_slug: str = f"{spec.parent_key}-{resolved.name}"
                return self._sync.ensure_table_alias(resolved, existing, raw_slug)
            case CatalogTableRow():
                return self._sync.ensure_table(resolved)
            case AirflowDagRow():
                return self._sync.ensure_dag(resolved)
            case AirflowInstanceRow():
                return self._sync.ensure_instance(resolved)
            case WorkspaceNodeRow():
                created: AssetRow | None = self._sync.ensure_file(resolved)
                if created is None:
                    raise CatalogError("folders are not assets")
                return created

    def _overlay(self, asset: AssetRow, spec: AssetSpec, file_key: str) -> None:
        if spec.parent_key is None:
            asset.name = _clip(spec.key)
        if "description" in spec.set_fields:
            asset.description = spec.description
        if "context" in spec.set_fields:
            apply_user_context(asset, spec.context)
        if "status" in spec.set_fields and spec.status is not None:
            asset.status = spec.status.value
        properties: dict[str, object] = dict(asset.properties)
        properties["definition"] = {"file": file_key, "key": spec.key}
        if "properties" in spec.set_fields:
            properties["declared"] = dict(spec.properties)
        if "tags" in spec.set_fields:
            properties["tags"] = list(spec.tags)
        asset.properties = properties
        asset.updated_at = utc_now()



    def _retire_item(self, item: AssetDefinitionItemRow) -> None:
        asset_id: UUID | None = item.asset_id if item.kind == DefinitionKind.ASSET.value else None
        if item.kind == DefinitionKind.ASSET.value:
            self._detach_claim(item)
        self._repo.delete_item(item)
        if asset_id is not None:
            self._delete_asset_if_undeclared(asset_id)

    def drop_undeclared_assets(self) -> int:
        """Delete assets no applied asset script still names."""
        declared: set[UUID] = self._repo.applied_asset_ids()
        removed: int = 0
        asset: AssetRow
        for asset in list(self._session.scalars(select(AssetRow))):
            if asset.id in declared:
                continue
            self._assets.delete(asset)
            removed += 1
        return removed

    def _delete_asset_if_undeclared(self, asset_id: UUID) -> None:
        if asset_id in self._repo.applied_asset_ids():
            return
        asset: AssetRow | None = self._session.get(AssetRow, asset_id)
        if asset is not None:
            self._assets.delete(asset)

    def _detach_claim(self, item: AssetDefinitionItemRow) -> None:
        del item

    def _record(
        self,
        file_row: AssetDefinitionFileRow,
        kind: DefinitionKind,
        item_key: str,
        spec: BaseModel,
        status: DefinitionItemStatus,
        error: str | None,
        prior: AssetDefinitionItemRow | None,
        *,
        asset_id: UUID | None = None,
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
                kind=kind.value,
                item_key=item_key,
                declaration=payload,
                fingerprint=digest,
                asset_id=asset_id,
                status=status.value,
                error=message,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
            self._session.flush()
            return created, True
        prior.kind = kind.value
        prior.item_key = item_key
        prior.declaration = payload
        prior.fingerprint = digest
        prior.asset_id = asset_id
        prior.status = status.value
        prior.error = message
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


def _declared(spec: DefinitionSpec) -> list[tuple[str, DefinitionKind, BaseModel]]:
    declared: list[tuple[str, DefinitionKind, BaseModel]] = []
    asset: AssetSpec
    for asset in spec.assets:
        declared.append((asset.key, DefinitionKind.ASSET, asset))
    return declared


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


def _clip(value: str) -> str:
    if len(value) <= 255:
        return value
    return value[:255]


def _clip_error(message: str | None) -> str | None:
    if message is None or len(message) <= _MAX_ERROR:
        return message
    return message[:_MAX_ERROR]
