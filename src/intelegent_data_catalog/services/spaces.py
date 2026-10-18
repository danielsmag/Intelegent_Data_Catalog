"""Space writes and reads."""

from uuid import UUID

from intelegent_data_catalog.exceptions import ConflictError, ValidationAppError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import SpaceIsolationMode
from intelegent_data_catalog.persistence.models import CatalogTableRow, ScopeRow, SpaceRow
from intelegent_data_catalog.registry.reads import space_read
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import SpaceCreate, SpaceRead, SpaceUpdate
from intelegent_data_catalog.services.support import (
    apply_user_context,
    require_row,
    required,
    slug_after_patch,
    slug_for,
)


class SpaceService:
    """Space persistence against the shared catalog session."""

    def __init__(self, repo: RegistryRepository) -> None:
        self._repo: RegistryRepository = repo

    def create_space(self, body: SpaceCreate, *, workspace_id: UUID | None = None) -> SpaceRead:
        """Create a space. An isolated space is bound to the active workspace."""
        slug: str = slug_for(body.name, body.slug)
        self._ensure_space_slug(slug, None)
        if body.isolation_mode == SpaceIsolationMode.ISOLATED and workspace_id is None:
            raise ValidationAppError(
                "isolated space needs an active workspace",
                field="isolation_mode",
            )
        row: SpaceRow = self._repo.add_space(
            name=body.name,
            slug=slug,
            description=body.description,
            isolation_mode=body.isolation_mode.value,
        )
        if body.isolation_mode == SpaceIsolationMode.ISOLATED and workspace_id is not None:
            self._repo.add_space_binding(workspace_id, row.id)
        self._repo.commit()
        return self._space_read(row)

    def list_spaces(self, visible_ids: frozenset[UUID] | None = None) -> list[SpaceRead]:
        """List spaces the caller may see. ``None`` returns every space."""
        allowed: set[UUID] | None = None if visible_ids is None else set(visible_ids)
        return self._space_reads(self._repo.list_spaces(allowed))

    def get_space(self, space_id: UUID) -> SpaceRead:
        """Return one space."""
        return self._space_read(self._space(space_id))

    def space_id_for_table(self, table_id: UUID) -> UUID:
        """Return the space that owns a table."""
        table: CatalogTableRow = self._table(table_id)
        scope: ScopeRow = self._scope(table.scope_id)
        return scope.space_id

    def update_space(
        self,
        space_id: UUID,
        patch: SpaceUpdate,
        *,
        workspace_id: UUID | None = None,
    ) -> SpaceRead:
        """Patch a space. A new name refreshes the slug unless the patch includes one."""
        row: SpaceRow = self._space(space_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = required(patch.name, "name")
        next_slug: str | None = slug_after_patch(row.name, changes, patch.slug)
        if next_slug is not None:
            self._ensure_space_slug(next_slug, row.id)
            row.slug = next_slug
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            apply_user_context(row, patch.context)
        if "isolation_mode" in changes and patch.isolation_mode is not None:
            row.isolation_mode = patch.isolation_mode.value
            if (
                patch.isolation_mode == SpaceIsolationMode.ISOLATED
                and self._repo.count_space_bindings(row.id) == 0
            ):
                if workspace_id is None:
                    raise ValidationAppError(
                        "isolated space needs an active workspace",
                        field="isolation_mode",
                    )
                self._repo.add_space_binding(workspace_id, row.id)
        row.updated_at = utc_now()
        self._repo.commit()
        return self._space_read(row)

    def delete_space(self, space_id: UUID) -> None:
        """Delete a space that has no scopes."""
        row: SpaceRow = self._space(space_id)
        if self._repo.count_scopes(space_id) > 0:
            raise ConflictError("space still has scopes")
        self._repo.delete(row)
        self._repo.commit()

    def _ensure_space_slug(self, slug: str, current_id: UUID | None) -> None:
        existing: SpaceRow | None = self._repo.find_space_by_slug(slug)
        if existing is not None and existing.id != current_id:
            raise ConflictError(f"space slug already exists: {slug}")

    def _space_read(self, row: SpaceRow) -> SpaceRead:
        bound: dict[UUID, list[UUID]] = self._repo.bindings_for_spaces([row.id])
        return space_read(row, bound.get(row.id, []))

    def _space_reads(self, rows: list[SpaceRow]) -> list[SpaceRead]:
        bound: dict[UUID, list[UUID]] = self._repo.bindings_for_spaces([row.id for row in rows])
        return [space_read(row, bound.get(row.id, [])) for row in rows]

    def _space(self, space_id: UUID) -> SpaceRow:
        row: SpaceRow | None = self._repo.get_space(space_id)
        return require_row(row, "space", space_id)

    def _scope(self, scope_id: UUID) -> ScopeRow:
        row: ScopeRow | None = self._repo.get_scope(scope_id)
        return require_row(row, "scope", scope_id)

    def _table(self, table_id: UUID) -> CatalogTableRow:
        row: CatalogTableRow | None = self._repo.get_table(table_id)
        return require_row(row, "table", table_id)
