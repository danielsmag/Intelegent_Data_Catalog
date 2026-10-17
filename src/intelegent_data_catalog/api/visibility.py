"""Hide catalog rows that the active workspace cannot see."""

from collections.abc import Mapping
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.persistence.enums import CatalogJobKind
from intelegent_data_catalog.persistence.models import (
    AirflowAttachmentRow,
    AssetEngineBindingRow,
    CatalogJobRow,
    CatalogTableRow,
    ColumnRow,
    RelationshipRow,
    ScopeRow,
)

_SPACE_PARAMS: frozenset[str] = frozenset(
    {
        "space_id",
        "scope_id",
        "table_id",
        "column_id",
        "relationship_id",
        "job_id",
        "attachment_id",
        "binding_id",
    }
)


class SpaceResolver:
    """Map a request path to the spaces it touches."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def enforce(self, params: Mapping[str, str], access: AccessContext) -> None:
        """404 when a path id belongs to a space the caller cannot see."""
        if access.visible_space_ids is None:
            return
        name: str
        raw: str
        for name, raw in params.items():
            if name not in _SPACE_PARAMS:
                continue
            try:
                entity_id: UUID = UUID(str(raw))
            except ValueError:
                continue
            self._check(name, entity_id, access)

    def space_for_job(self, job: CatalogJobRow) -> UUID | None:
        """Return the space a background job belongs to, when it has one."""
        subject: UUID = job.subject_id
        if job.kind == CatalogJobKind.SPACE.value:
            return subject
        if job.kind == CatalogJobKind.SCOPE.value:
            return self._space_of_scope(subject)
        if job.kind == CatalogJobKind.KNOWLEDGE.value:
            return self._space_of_table(subject)
        if job.kind == CatalogJobKind.CONTEXT.value:
            return self._space_of_subject(subject)
        return None

    def _check(self, name: str, entity_id: UUID, access: AccessContext) -> None:
        space_id: UUID | None
        if name == "space_id":
            if not access.allows_space(entity_id):
                raise EntityNotFoundError("space", str(entity_id))
            return
        if name == "scope_id":
            space_id = self._space_of_scope(entity_id)
            self._require(space_id, "scope", entity_id, access)
            return
        if name == "table_id":
            space_id = self._space_of_table(entity_id)
            self._require(space_id, "table", entity_id, access)
            return
        if name == "column_id":
            space_id = self._space_of_column(entity_id)
            self._require(space_id, "column", entity_id, access)
            return
        if name == "binding_id":
            binding: AssetEngineBindingRow | None = self._session.get(
                AssetEngineBindingRow,
                entity_id,
            )
            if binding is None:
                return
            space_id = self._space_of_table(binding.catalog_table_id)
            self._require(space_id, "binding", entity_id, access)
            return
        if name == "relationship_id":
            relationship: RelationshipRow | None = self._session.get(RelationshipRow, entity_id)
            if relationship is None:
                return
            source_space: UUID | None = self._space_of_table(relationship.source_table_id)
            target_space: UUID | None = self._space_of_table(relationship.target_table_id)
            self._require(source_space, "relationship", entity_id, access)
            self._require(target_space, "relationship", entity_id, access)
            return
        if name == "attachment_id":
            attachment: AirflowAttachmentRow | None = self._session.get(
                AirflowAttachmentRow,
                entity_id,
            )
            if attachment is None:
                return
            if attachment.space_id is not None and not access.allows_space(attachment.space_id):
                raise EntityNotFoundError("attachment", str(entity_id))
            if attachment.scope_id is not None:
                scope_space: UUID | None = self._space_of_scope(attachment.scope_id)
                self._require(scope_space, "attachment", entity_id, access)
            return
        if name == "job_id":
            job: CatalogJobRow | None = self._session.get(CatalogJobRow, entity_id)
            if job is None:
                return
            space_id = self.space_for_job(job)
            if space_id is not None and not access.allows_space(space_id):
                raise EntityNotFoundError("job", str(entity_id))

    def _require(
        self,
        space_id: UUID | None,
        entity: str,
        entity_id: UUID,
        access: AccessContext,
    ) -> None:
        if space_id is None:
            return
        if not access.allows_space(space_id):
            raise EntityNotFoundError(entity, str(entity_id))

    def _space_of_subject(self, subject_id: UUID) -> UUID | None:
        if self._session.get(ScopeRow, subject_id) is not None:
            return self._space_of_scope(subject_id)
        if self._session.get(CatalogTableRow, subject_id) is not None:
            return self._space_of_table(subject_id)
        if self._session.get(ColumnRow, subject_id) is not None:
            return self._space_of_column(subject_id)
        return subject_id if self._space_row_exists(subject_id) else None

    def _space_row_exists(self, space_id: UUID) -> bool:
        from intelegent_data_catalog.persistence.models import SpaceRow

        return self._session.get(SpaceRow, space_id) is not None

    def _space_of_scope(self, scope_id: UUID) -> UUID | None:
        scope: ScopeRow | None = self._session.get(ScopeRow, scope_id)
        if scope is None:
            return None
        return scope.space_id

    def _space_of_table(self, table_id: UUID) -> UUID | None:
        table: CatalogTableRow | None = self._session.get(CatalogTableRow, table_id)
        if table is None:
            return None
        return self._space_of_scope(table.scope_id)

    def _space_of_column(self, column_id: UUID) -> UUID | None:
        column: ColumnRow | None = self._session.get(ColumnRow, column_id)
        if column is None:
            return None
        return self._space_of_table(column.table_id)


def enforce_visibility(request: Request, access: AccessContext, session: Session) -> None:
    """Apply the workspace visibility rule to path parameters."""
    SpaceResolver(session).enforce(request.path_params, access)


def filter_trace_job_ids(
    session: Session,
    jobs: list[CatalogJobRow],
    access: AccessContext,
) -> set[UUID]:
    """Return job ids whose space the caller may see. Jobs without a space stay."""
    if access.visible_space_ids is None:
        return {row.id for row in jobs}
    resolver: SpaceResolver = SpaceResolver(session)
    allowed: set[UUID] = set()
    row: CatalogJobRow
    for row in jobs:
        space_id: UUID | None = resolver.space_for_job(row)
        if space_id is None or access.allows_space(space_id):
            allowed.add(row.id)
    return allowed


def visible_scope_ids(session: Session, access: AccessContext) -> set[UUID] | None:
    """Return scope ids in visible spaces. ``None`` means every scope."""
    if access.visible_space_ids is None:
        return None
    if len(access.visible_space_ids) == 0:
        return set()
    return set(
        session.scalars(
            select(ScopeRow.id).where(ScopeRow.space_id.in_(access.visible_space_ids))
        )
    )
