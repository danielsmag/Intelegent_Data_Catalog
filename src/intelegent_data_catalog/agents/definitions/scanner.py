"""Poll the definition prefix and apply files whose bytes changed."""

import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.definitions.applier import AgentDefinitionApplier
from intelegent_data_catalog.agents.definitions.layout import (
    KIND_RANK,
    DefinitionLayout,
    DefinitionLocation,
)
from intelegent_data_catalog.agents.definitions.models import DefinitionSpec, ScanResult
from intelegent_data_catalog.agents.definitions.repository import AgentDefinitionRepository
from intelegent_data_catalog.agents.dsl.collect import collect_definitions
from intelegent_data_catalog.agents.dsl.errors import AgentDefinitionImportError
from intelegent_data_catalog.agents.execution.executor import OperatorExecutor
from intelegent_data_catalog.agents.execution.workload import OperatorWorkload
from intelegent_data_catalog.core.settings import AgentDefinitionsSettings
from intelegent_data_catalog.persistence.enums import DefinitionFileStatus, DefinitionKind
from intelegent_data_catalog.persistence.models import AgentDefinitionFileRow
from intelegent_data_catalog.storage.object_store import ObjectStore, StoredObject

_MAX_ERROR: int = 8000


class AgentDefinitionScanner:
    """Lists the prefix, executes changed Python files, and retries unresolved items."""

    def __init__(
        self,
        session: Session,
        store: ObjectStore,
        settings: AgentDefinitionsSettings,
        logger: logging.Logger,
        executor: OperatorExecutor,
    ) -> None:
        self._session: Session = session
        self._store: ObjectStore = store
        self._settings: AgentDefinitionsSettings = settings
        self._logger: logging.Logger = logger
        self._executor: OperatorExecutor = executor
        self._layout: DefinitionLayout = DefinitionLayout(settings)
        self._repo: AgentDefinitionRepository = AgentDefinitionRepository(session)
        self._applier: AgentDefinitionApplier = AgentDefinitionApplier(session, logger)

    def scan(self, now: datetime) -> ScanResult:
        """Apply new and changed files, retire missing keys, then retry unresolved items."""
        listed: list[tuple[StoredObject, DefinitionLocation]] = []
        stored: StoredObject
        for stored in self._store.list(self._settings.prefix):
            location: DefinitionLocation | None = self._layout.locate(stored.key)
            if location is None:
                continue
            listed.append((stored, location))
        listed.sort(key=_location_sort)
        seen_keys: set[str] = set()
        parsed: int = 0
        errors: int = 0
        pair: tuple[StoredObject, DefinitionLocation]
        for pair in listed:
            seen_keys.add(pair[0].key)
            outcome: str = self._process(pair[0], pair[1], now)
            if outcome == "parsed":
                parsed += 1
            elif outcome == "error":
                errors += 1
        removed: int = self._remove_absent(seen_keys, now)
        retried: int = self._applier.retry_unresolved()
        refreshed: int = self._applier.refresh_schemas()
        dropped: int = self._applier.drop_undeclared_agents()
        retried_work: list[OperatorWorkload] = self._applier.take_workloads()
        self._session.commit()
        self._submit(retried_work)
        result: ScanResult = ScanResult(
            seen=len(listed),
            parsed=parsed,
            removed=removed,
            errors=errors,
            retried=retried,
        )
        self._logger.info(
            "agent definition scan seen=%s parsed=%s removed=%s errors=%s retried=%s "
            "refreshed=%s dropped=%s",
            result.seen,
            result.parsed,
            result.removed,
            result.errors,
            result.retried,
            refreshed,
            dropped,
        )
        return result

    def _process(self, obj: StoredObject, location: DefinitionLocation, now: datetime) -> str:
        if not location.name_ok:
            self._write_error(
                obj,
                location.kind,
                f"file name must start with `{location.name_prefix}`",
                now,
            )
            return "error"
        if obj.size_bytes > self._settings.max_file_bytes:
            self._write_error(
                obj,
                location.kind,
                f"file exceeds {self._settings.max_file_bytes} bytes",
                now,
            )
            return "error"
        existing: AgentDefinitionFileRow | None = self._repo.get_by_key(obj.key)
        if _unchanged(existing, obj.etag):
            if existing is not None:
                existing.folder_kind = location.kind.value
                existing.scanned_at = now
                existing.updated_at = now
                self._session.commit()
            return "skipped"
        try:
            payload: bytes = self._store.get(obj.key)
            source: str = payload.decode("utf-8", errors="replace")
            spec: DefinitionSpec = collect_definitions(
                source,
                location.filename,
                location.kind,
                timeout_seconds=self._settings.exec_timeout_seconds,
            )
            row: AgentDefinitionFileRow = self._upsert(obj, location.kind, now)
            self._applier.apply_file(row, spec)
            row.status = DefinitionFileStatus.OK.value
            row.error = None
            row.parsed_at = now
            row.item_count = len(self._repo.items_for(row.id))
            row.updated_at = now
            workloads: list[OperatorWorkload] = self._applier.take_workloads()
            self._session.commit()
            self._submit(workloads)
            return "parsed"
        except AgentDefinitionImportError as err:
            self._applier.drop_workloads()
            self._session.rollback()
            self._write_error(obj, location.kind, _import_message(err), now)
            return "error"
        except Exception:
            self._logger.exception("agent definition file failed: %s", obj.key)
            self._applier.drop_workloads()
            self._session.rollback()
            self._write_error(obj, location.kind, "definition file failed", now)
            return "error"

    def _submit(self, workloads: list[OperatorWorkload]) -> None:
        workload: OperatorWorkload
        for workload in workloads:
            try:
                self._executor.submit(workload)
            except Exception:
                self._logger.exception("agent operator submit failed: %s", workload.item_key)

    def _remove_absent(self, seen: set[str], now: datetime) -> int:
        removed: int = 0
        row: AgentDefinitionFileRow
        for row in self._repo.list_files(None):
            if row.status == DefinitionFileStatus.REMOVED.value or row.key in seen:
                continue
            key: str = row.key
            try:
                self._applier.retire_file(row)
                row.status = DefinitionFileStatus.REMOVED.value
                row.error = None
                row.item_count = 0
                row.scanned_at = now
                row.updated_at = now
                self._session.commit()
                removed += 1
            except Exception:
                self._logger.exception("agent definition removal failed: %s", key)
                self._session.rollback()
        return removed

    def _upsert(
        self,
        obj: StoredObject,
        kind: DefinitionKind,
        now: datetime,
    ) -> AgentDefinitionFileRow:
        row: AgentDefinitionFileRow | None = self._repo.get_by_key(obj.key)
        if row is None:
            return self._repo.add_file(_new_file(obj, kind, now, DefinitionFileStatus.OK, None))
        row.folder_kind = kind.value
        row.etag = obj.etag
        row.size_bytes = obj.size_bytes
        row.last_modified = obj.last_modified
        row.scanned_at = now
        row.updated_at = now
        self._session.flush()
        return row

    def _write_error(
        self,
        obj: StoredObject,
        kind: DefinitionKind,
        message: str,
        now: datetime,
    ) -> None:
        text: str = message[:_MAX_ERROR]
        row: AgentDefinitionFileRow | None = self._repo.get_by_key(obj.key)
        if row is None:
            row = self._repo.add_file(_new_file(obj, kind, now, DefinitionFileStatus.ERROR, text))
        else:
            row.folder_kind = kind.value
            row.etag = obj.etag
            row.size_bytes = obj.size_bytes
            row.last_modified = obj.last_modified
            row.status = DefinitionFileStatus.ERROR.value
            row.error = text
            row.scanned_at = now
            row.updated_at = now
            row.item_count = len(self._repo.items_for(row.id))
        self._session.commit()


def _unchanged(existing: AgentDefinitionFileRow | None, etag: str) -> bool:
    if existing is None:
        return False
    if existing.etag != etag:
        return False
    return existing.status in {
        DefinitionFileStatus.OK.value,
        DefinitionFileStatus.ERROR.value,
    }


def _new_file(
    obj: StoredObject,
    kind: DefinitionKind,
    now: datetime,
    status: DefinitionFileStatus,
    error: str | None,
) -> AgentDefinitionFileRow:
    return AgentDefinitionFileRow(
        id=uuid4(),
        key=obj.key,
        folder_kind=kind.value,
        etag=obj.etag,
        size_bytes=obj.size_bytes,
        last_modified=obj.last_modified,
        status=status.value,
        error=error,
        item_count=0,
        scanned_at=now,
        parsed_at=None,
        created_at=now,
        updated_at=now,
    )


def _import_message(err: AgentDefinitionImportError) -> str:
    if err.traceback_text.strip() == "":
        return err.message
    return f"{err.message}\n{err.traceback_text}"


def _location_sort(item: tuple[StoredObject, DefinitionLocation]) -> tuple[int, str]:
    stored: StoredObject = item[0]
    location: DefinitionLocation = item[1]
    return (KIND_RANK[location.kind.value], stored.key)
