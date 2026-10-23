"""Apply declarations: ensure spaces and scopes, link assets, drop removed memberships."""

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
    ScopeSpec,
    SpaceSpec,
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
    SpaceIsolationMode,
)
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
    AssetMembershipRow,
    AssetRow,
    CatalogTableRow,
    ScopeRow,
    SpaceRow,
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
        for key, prior in existing.items():
            if key not in declared_keys:
                self._retire_item(prior)
        for item_key, kind, model in declared:
            prior_item: AssetDefinitionItemRow | None = existing.get(item_key)
            if _unchanged(prior_item, model):
                continue
            self._apply_model(file_row, kind, model, prior_item)

    def retire_file(self, file_row: AssetDefinitionFileRow) -> None:
        """Detach memberships this file created and delete its items. Spaces and scopes stay."""
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
            except CatalogError, ValidationError, SQLAlchemyError:
                self._logger.exception("asset definition retry failed: %s", item_id)
                self._pending = self._pending[:kept]
                self._session.rollback()
                self._mark_retry_error(item_id)
                self._session.commit()
                continue
            if status == DefinitionItemStatus.APPLIED:
                applied += 1
        return applied

    def _retry(self, item: AssetDefinitionItemRow) -> DefinitionItemStatus:
        file_row: AssetDefinitionFileRow | None = self._session.get(
            AssetDefinitionFileRow,
            item.file_id,
        )
        if file_row is None or file_row.status == DefinitionFileStatus.REMOVED.value:
            return DefinitionItemStatus.UNRESOLVED
        kind: DefinitionKind = DefinitionKind(item.kind)
        match kind:
            case DefinitionKind.SPACE:
                return self._apply_space(file_row, SpaceSpec.model_validate(item.declaration), item)
            case DefinitionKind.SCOPE:
                return self._apply_scope(file_row, ScopeSpec.model_validate(item.declaration), item)
            case DefinitionKind.ASSET:
                return self._apply_asset(file_row, AssetSpec.model_validate(item.declaration), item)

    def _apply_model(
        self,
        file_row: AssetDefinitionFileRow,
        kind: DefinitionKind,
        model: BaseModel,
        prior: AssetDefinitionItemRow | None,
    ) -> None:
        match kind:
            case DefinitionKind.SPACE if isinstance(model, SpaceSpec):
                self._apply_space(file_row, model, prior)
            case DefinitionKind.SCOPE if isinstance(model, ScopeSpec):
                self._apply_scope(file_row, model, prior)
            case DefinitionKind.ASSET if isinstance(model, AssetSpec):
                self._apply_asset(file_row, model, prior)
            case _:
                raise CatalogError(f"declaration kind does not match {kind.value}")

    def _apply_space(
        self,
        file_row: AssetDefinitionFileRow,
        spec: SpaceSpec,
        prior: AssetDefinitionItemRow | None,
    ) -> DefinitionItemStatus:
        row: SpaceRow | None = self._registry.find_space_by_slug(spec.slug)
        if row is None:
            label: str = spec.slug if spec.name is None else spec.name
            description: str | None = spec.description if "description" in spec.set_fields else None
            row = self._registry.add_space(
                name=_clip(label),
                slug=spec.slug,
                description=description,
                isolation_mode=SpaceIsolationMode.OPEN.value,
            )
        self._patch_space(row, spec)
        self._record(
            file_row,
            DefinitionKind.SPACE,
            spec.slug,
            spec,
            DefinitionItemStatus.APPLIED,
            None,
            prior,
            space_id=row.id,
        )
        return DefinitionItemStatus.APPLIED

    def _apply_scope(
        self,
        file_row: AssetDefinitionFileRow,
        spec: ScopeSpec,
        prior: AssetDefinitionItemRow | None,
    ) -> DefinitionItemStatus:
        item_key: str = f"{spec.space_slug}/{spec.slug}"
        space: SpaceRow | None = self._registry.find_space_by_slug(spec.space_slug)
        if space is None:
            self._record(
                file_row,
                DefinitionKind.SCOPE,
                item_key,
                spec,
                DefinitionItemStatus.UNRESOLVED,
                f"space not found: {spec.space_slug}",
                prior,
            )
            return DefinitionItemStatus.UNRESOLVED
        scope: ScopeRow | None = self._registry.find_scope_by_slug(space.id, spec.slug)
        if scope is None:
            label: str = spec.slug if spec.name is None else spec.name
            description: str | None = spec.description if "description" in spec.set_fields else None
            scope = self._registry.add_scope(
                space_id=space.id,
                name=_clip(label),
                slug=spec.slug,
                description=description,
            )
        self._patch_scope(scope, spec)
        self._record(
            file_row,
            DefinitionKind.SCOPE,
            item_key,
            spec,
            DefinitionItemStatus.APPLIED,
            None,
            prior,
            space_id=space.id,
            scope_id=scope.id,
        )
        return DefinitionItemStatus.APPLIED

    def _apply_asset(
        self,
        file_row: AssetDefinitionFileRow,
        spec: AssetSpec,
        prior: AssetDefinitionItemRow | None,
    ) -> DefinitionItemStatus:
        resolved: ResolvedSource | UnresolvedSource | RejectedSource = self._resolver.resolve(
            spec.source,
        )
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
        asset: AssetRow = self._ensure_asset(resolved)
        self._overlay(asset, spec, file_row.key)
        if prior is not None and (prior.scope_id is not None or prior.space_id is not None):
            self._detach_claim(prior)
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

    def _ensure_asset(self, resolved: ResolvedSource) -> AssetRow:
        match resolved:
            case CatalogTableRow():
                return self._sync.ensure_table(resolved)
            case AirflowDagRow():
                return self._sync.ensure_dag(resolved)
            case WorkspaceNodeRow():
                created: AssetRow | None = self._sync.ensure_file(resolved)
                if created is None:
                    raise CatalogError("folders are not assets")
                return created

    def _overlay(self, asset: AssetRow, spec: AssetSpec, file_key: str) -> None:
        if "name" in spec.set_fields and spec.name is not None:
            asset.name = _clip(spec.name)
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
        asset.properties = properties
        asset.updated_at = utc_now()

    def _patch_space(self, row: SpaceRow, spec: SpaceSpec) -> None:
        changed: bool = False
        if "name" in spec.set_fields and spec.name is not None and row.name != spec.name:
            row.name = _clip(spec.name)
            changed = True
        if "description" in spec.set_fields and row.description != spec.description:
            row.description = spec.description
            changed = True
        if "context" in spec.set_fields:
            before: str | None = row.context
            before_origin: str | None = row.context_origin
            apply_user_context(row, spec.context)
            if row.context != before or row.context_origin != before_origin:
                changed = True
        if changed:
            row.updated_at = utc_now()

    def _patch_scope(self, row: ScopeRow, spec: ScopeSpec) -> None:
        changed: bool = False
        if "name" in spec.set_fields and spec.name is not None and row.name != spec.name:
            row.name = _clip(spec.name)
            changed = True
        if "description" in spec.set_fields and row.description != spec.description:
            row.description = spec.description
            changed = True
        if "context" in spec.set_fields:
            before: str | None = row.context
            before_origin: str | None = row.context_origin
            apply_user_context(row, spec.context)
            if row.context != before or row.context_origin != before_origin:
                changed = True
        if changed:
            row.updated_at = utc_now()

    def _retire_item(self, item: AssetDefinitionItemRow) -> None:
        asset_id: UUID | None = item.asset_id if item.kind == DefinitionKind.ASSET.value else None
        if item.kind == DefinitionKind.ASSET.value:
            self._detach_claim(item)
        self._repo.delete_item(item)
        if asset_id is not None:
            self._delete_asset_if_undeclared(asset_id)

    def drop_undeclared_assets(self) -> int:
        """Delete assets no applied asset script still names. Spaces and scopes stay."""
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
        if item.asset_id is None or item.status != DefinitionItemStatus.APPLIED.value:
            return
        if item.scope_id is None and item.space_id is None:
            return
        if self._repo.other_applied_claim(item):
            return
        membership: AssetMembershipRow | None = self._assets.membership_for(
            item.asset_id,
            scope_id=item.scope_id,
            space_id=item.space_id,
        )
        if membership is not None:
            self._assets.detach(membership)

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
        space_id: UUID | None = None,
        scope_id: UUID | None = None,
        asset_id: UUID | None = None,
    ) -> tuple[AssetDefinitionItemRow, bool]:
        digest: str = fingerprint(spec)
        payload: dict[str, object] = declaration_payload(spec)
        message: str | None = _clip_error(error)
        if prior is not None and _same_record(
            prior,
            status,
            digest,
            message,
            space_id,
            scope_id,
            asset_id,
        ):
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
                space_id=space_id,
                scope_id=scope_id,
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
        prior.space_id = space_id
        prior.scope_id = scope_id
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
    space: SpaceSpec
    for space in spec.spaces:
        declared.append((space.slug, DefinitionKind.SPACE, space))
    scope: ScopeSpec
    for scope in spec.scopes:
        declared.append((f"{scope.space_slug}/{scope.slug}", DefinitionKind.SCOPE, scope))
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
    space_id: UUID | None,
    scope_id: UUID | None,
    asset_id: UUID | None,
) -> bool:
    return (
        prior.status == status.value
        and prior.fingerprint == digest
        and prior.error == message
        and prior.space_id == space_id
        and prior.scope_id == scope_id
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
