"""Apply agent declarations and drop agents no file still names."""

import logging
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.definitions.fingerprint import declaration_payload, fingerprint
from intelegent_data_catalog.agents.definitions.models import (
    AgentSpec,
    DefinitionSpec,
    SchemaSourceRef,
)
from intelegent_data_catalog.agents.definitions.repository import AgentDefinitionRepository
from intelegent_data_catalog.agents.definitions.resolver import (
    RejectedSource,
    ResolvedSource,
    SourceResolver,
    UnresolvedSource,
)
from intelegent_data_catalog.agents.execution.workload import OperatorWorkload
from intelegent_data_catalog.agents.repository import AgentRepository
from intelegent_data_catalog.agents.sync import AgentSync
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    DefinitionFileStatus,
    DefinitionItemStatus,
    DefinitionKind,
    DefinitionRunStatus,
)
from intelegent_data_catalog.persistence.models import (
    AgentDefinitionFileRow,
    AgentDefinitionItemRow,
    AgentRow,
    AirflowDagRow,
    AirflowInstanceRow,
    CatalogTableRow,
    WorkspaceNodeRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.services.support import apply_user_context

_MAX_ERROR: int = 8000


class AgentDefinitionApplier:
    """Writes catalog rows for one file. The scanner commits. Retries commit per item."""

    def __init__(self, session: Session, logger: logging.Logger) -> None:
        self._session: Session = session
        self._logger: logging.Logger = logger
        self._repo: AgentDefinitionRepository = AgentDefinitionRepository(session)
        self._registry: RegistryRepository = RegistryRepository(session)
        self._agents: AgentRepository = AgentRepository(session)
        self._resolver: SourceResolver = SourceResolver(session)
        self._sync: AgentSync = AgentSync(session)
        self._pending: list[OperatorWorkload] = []

    def take_workloads(self) -> list[OperatorWorkload]:
        """Return operator runs collected since the last take, then clear them."""
        found: list[OperatorWorkload] = list(self._pending)
        self._pending.clear()
        return found

    def drop_workloads(self) -> None:
        """Forget operator runs collected for a file that failed."""
        self._pending.clear()

    def apply_file(self, file_row: AgentDefinitionFileRow, spec: DefinitionSpec) -> None:
        """Upsert declarations and retire ones the file no longer contains."""
        existing: dict[str, AgentDefinitionItemRow] = {
            item.item_key: item for item in self._repo.items_for(file_row.id)
        }
        declared: list[tuple[str, DefinitionKind, BaseModel]] = _declared(spec)
        declared_keys: set[str] = {item[0] for item in declared}
        schema_keys: set[str] = {
            agent.key for agent in spec.agents if isinstance(agent.source, SchemaSourceRef)
        }
        for key, prior in existing.items():
            if key in declared_keys or key.split("/", 1)[0] in schema_keys:
                continue
            self._retire_item(prior)
        for item_key, kind, model in declared:
            prior_item: AgentDefinitionItemRow | None = existing.get(item_key)
            schema_agent: bool = isinstance(model, AgentSpec) and isinstance(
                model.source,
                SchemaSourceRef,
            )
            if not schema_agent and _unchanged(prior_item, model):
                continue
            self._apply_model(file_row, kind, model, prior_item)

    def retire_file(self, file_row: AgentDefinitionFileRow) -> None:
        """Delete the items this file created."""
        item: AgentDefinitionItemRow
        for item in self._repo.items_for(file_row.id):
            self._retire_item(item)

    def retry_unresolved(self) -> int:
        """Re-apply unresolved items from their stored declaration. Each item commits."""
        applied: int = 0
        item: AgentDefinitionItemRow
        for item in self._repo.unresolved():
            item_id: UUID = item.id
            kept: int = len(self._pending)
            try:
                status: DefinitionItemStatus = self._retry(item)
                self._session.commit()
            except (CatalogError, ValidationError, SQLAlchemyError):
                self._logger.exception("agent definition retry failed: %s", item_id)
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
        item: AgentDefinitionItemRow
        for item in self._repo.schema_parents():
            item_id: UUID = item.id
            kept: int = len(self._pending)
            try:
                self._refresh_schema(item)
                self._session.commit()
            except (CatalogError, ValidationError, SQLAlchemyError):
                self._logger.exception("agent schema refresh failed: %s", item_id)
                self._pending = self._pending[:kept]
                self._session.rollback()
                self._mark_retry_error(item_id)
                self._session.commit()
                continue
            refreshed += 1
        return refreshed

    def _refresh_schema(self, item: AgentDefinitionItemRow) -> None:
        file_row: AgentDefinitionFileRow | None = self._session.get(
            AgentDefinitionFileRow,
            item.file_id,
        )
        if file_row is None or file_row.status == DefinitionFileStatus.REMOVED.value:
            return
        spec: AgentSpec = AgentSpec.model_validate(item.declaration)
        self._apply_schema(file_row, spec, item)

    def _retry(self, item: AgentDefinitionItemRow) -> DefinitionItemStatus:
        file_row: AgentDefinitionFileRow | None = self._session.get(
            AgentDefinitionFileRow,
            item.file_id,
        )
        if file_row is None or file_row.status == DefinitionFileStatus.REMOVED.value:
            return DefinitionItemStatus.UNRESOLVED
        kind: DefinitionKind = DefinitionKind(item.kind)
        if kind is not DefinitionKind.AGENT:
            return DefinitionItemStatus.ERROR
        return self._apply_agent(file_row, AgentSpec.model_validate(item.declaration), item)

    def _apply_model(
        self,
        file_row: AgentDefinitionFileRow,
        kind: DefinitionKind,
        model: BaseModel,
        prior: AgentDefinitionItemRow | None,
    ) -> None:
        if kind is DefinitionKind.AGENT and isinstance(model, AgentSpec):
            self._apply_agent(file_row, model, prior)
            return
        raise CatalogError(f"declaration kind does not match {kind.value}")



    def _apply_agent(
        self,
        file_row: AgentDefinitionFileRow,
        spec: AgentSpec,
        prior: AgentDefinitionItemRow | None,
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
                DefinitionKind.AGENT,
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
                DefinitionKind.AGENT,
                spec.key,
                spec,
                DefinitionItemStatus.ERROR,
                resolved.reason,
                prior,
            )
            return DefinitionItemStatus.ERROR
        agent: AgentRow = self._ensure_agent(resolved, spec, prior)
        self._overlay(agent, spec, file_row.key)
        recorded: AgentDefinitionItemRow
        changed: bool
        recorded, changed = self._record(
            file_row,
            DefinitionKind.AGENT,
            spec.key,
            spec,
            DefinitionItemStatus.APPLIED,
            None,
            prior,
            agent_id=agent.id,
        )
        if changed and len(spec.operators) > 0:
            self._queue(file_row, recorded, agent, spec)
        return DefinitionItemStatus.APPLIED

    def _apply_schema(
        self,
        file_row: AgentDefinitionFileRow,
        spec: AgentSpec,
        prior: AgentDefinitionItemRow | None,
    ) -> DefinitionItemStatus:
        """Record one schema agent. Leftover per-table children are retired."""
        if not isinstance(spec.source, SchemaSourceRef):
            raise CatalogError("schema declaration has no schema source")
        resolved: ResolvedSource | UnresolvedSource | RejectedSource | list[CatalogTableRow]
        resolved = self._resolver.resolve(spec.source)
        self._retire_schema_children(file_row, spec.key)
        if isinstance(resolved, UnresolvedSource):
            if prior is not None:
                self._detach_claim(prior)
            self._record(
                file_row,
                DefinitionKind.AGENT,
                spec.key,
                spec,
                DefinitionItemStatus.UNRESOLVED,
                resolved.reason,
                prior,
            )
            return DefinitionItemStatus.UNRESOLVED
        if not isinstance(resolved, list):
            raise CatalogError("schema did not resolve to tables")
        existing: AgentRow | None = None
        if prior is not None and prior.agent_id is not None:
            existing = self._session.get(AgentRow, prior.agent_id)
        agent: AgentRow = self._sync.ensure_schema(spec, existing)
        self._overlay(agent, spec, file_row.key)
        recorded: AgentDefinitionItemRow
        changed: bool
        recorded, changed = self._record(
            file_row,
            DefinitionKind.AGENT,
            spec.key,
            spec,
            DefinitionItemStatus.APPLIED,
            None,
            prior,
            agent_id=agent.id,
        )
        if changed and len(spec.operators) > 0:
            self._queue(file_row, recorded, agent, spec)
        return DefinitionItemStatus.APPLIED

    def _retire_schema_children(self, file_row: AgentDefinitionFileRow, key: str) -> None:
        """Drop per-table items left by the old schema fan-out."""
        prefix: str = f"{key}/"
        child: AgentDefinitionItemRow
        for child in self._repo.items_for(file_row.id):
            if child.item_key.startswith(prefix):
                self._retire_item(child)

    def _queue(
        self,
        file_row: AgentDefinitionFileRow,
        item: AgentDefinitionItemRow,
        agent: AgentRow,
        spec: AgentSpec,
    ) -> None:
        if len(spec.operators) == 0:
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
                agent_id=agent.id,
                agent_name=agent.name,
                file_key=file_row.key,
                item_key=spec.key,
                operators=list(spec.operators),
            ),
        )

    def _ensure_agent(
        self,
        resolved: ResolvedSource,
        spec: AgentSpec,
        prior: AgentDefinitionItemRow | None,
    ) -> AgentRow:
        match resolved:
            case CatalogTableRow():
                return self._sync.ensure_table(resolved)
            case AirflowDagRow():
                return self._sync.ensure_dag(resolved)
            case AirflowInstanceRow():
                return self._sync.ensure_instance(resolved)
            case WorkspaceNodeRow():
                created: AgentRow | None = self._sync.ensure_file(resolved)
                if created is None:
                    raise CatalogError("folders are not agents")
                return created

    def _overlay(self, agent: AgentRow, spec: AgentSpec, file_key: str) -> None:
        agent.name = _clip(spec.key)
        if "description" in spec.set_fields:
            agent.description = spec.description
        if "context" in spec.set_fields:
            apply_user_context(agent, spec.context)
        if "status" in spec.set_fields and spec.status is not None:
            agent.status = spec.status.value
        properties: dict[str, object] = dict(agent.properties)
        properties["definition"] = {"file": file_key, "key": spec.key}
        if "properties" in spec.set_fields:
            properties["declared"] = dict(spec.properties)
        if "tags" in spec.set_fields:
            properties["tags"] = list(spec.tags)
        agent.properties = properties
        agent.updated_at = utc_now()



    def _retire_item(self, item: AgentDefinitionItemRow) -> None:
        agent_id: UUID | None = item.agent_id if item.kind == DefinitionKind.AGENT.value else None
        if item.kind == DefinitionKind.AGENT.value:
            self._detach_claim(item)
        self._repo.delete_item(item)
        if agent_id is not None:
            self._delete_agent_if_undeclared(agent_id)

    def drop_undeclared_agents(self) -> int:
        """Delete agents no applied agent script still names."""
        declared: set[UUID] = self._repo.applied_agent_ids()
        removed: int = 0
        agent: AgentRow
        for agent in list(self._session.scalars(select(AgentRow))):
            if agent.id in declared:
                continue
            self._agents.delete(agent)
            removed += 1
        return removed

    def _delete_agent_if_undeclared(self, agent_id: UUID) -> None:
        if agent_id in self._repo.applied_agent_ids():
            return
        agent: AgentRow | None = self._session.get(AgentRow, agent_id)
        if agent is not None:
            self._agents.delete(agent)

    def _detach_claim(self, item: AgentDefinitionItemRow) -> None:
        del item

    def _record(
        self,
        file_row: AgentDefinitionFileRow,
        kind: DefinitionKind,
        item_key: str,
        spec: BaseModel,
        status: DefinitionItemStatus,
        error: str | None,
        prior: AgentDefinitionItemRow | None,
        *,
        agent_id: UUID | None = None,
    ) -> tuple[AgentDefinitionItemRow, bool]:
        digest: str = fingerprint(spec)
        payload: dict[str, object] = declaration_payload(spec)
        message: str | None = _clip_error(error)
        if prior is not None and _same_record(prior, status, digest, message, agent_id):
            return prior, False
        now: datetime = utc_now()
        if prior is None:
            created: AgentDefinitionItemRow = AgentDefinitionItemRow(
                id=uuid4(),
                file_id=file_row.id,
                kind=kind.value,
                item_key=item_key,
                declaration=payload,
                fingerprint=digest,
                agent_id=agent_id,
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
        prior.agent_id = agent_id
        prior.status = status.value
        prior.error = message
        prior.updated_at = now
        self._session.flush()
        return prior, True

    def _mark_retry_error(self, item_id: UUID) -> None:
        row: AgentDefinitionItemRow | None = self._session.get(AgentDefinitionItemRow, item_id)
        if row is None:
            return
        row.status = DefinitionItemStatus.ERROR.value
        row.error = _clip_error("retry failed")
        row.updated_at = utc_now()
        self._session.flush()


def _declared(spec: DefinitionSpec) -> list[tuple[str, DefinitionKind, BaseModel]]:
    declared: list[tuple[str, DefinitionKind, BaseModel]] = []
    agent: AgentSpec
    for agent in spec.agents:
        declared.append((agent.key, DefinitionKind.AGENT, agent))
    return declared


def _unchanged(prior: AgentDefinitionItemRow | None, model: BaseModel) -> bool:
    if prior is None:
        return False
    applied: bool = prior.status == DefinitionItemStatus.APPLIED.value
    return applied and prior.fingerprint == fingerprint(model)


def _same_record(
    prior: AgentDefinitionItemRow,
    status: DefinitionItemStatus,
    digest: str,
    message: str | None,
    agent_id: UUID | None,
) -> bool:
    return (
        prior.status == status.value
        and prior.fingerprint == digest
        and prior.error == message
        and prior.agent_id == agent_id
    )


def _clip(value: str) -> str:
    if len(value) <= 255:
        return value
    return value[:255]


def _clip_error(message: str | None) -> str | None:
    if message is None or len(message) <= _MAX_ERROR:
        return message
    return message[:_MAX_ERROR]
