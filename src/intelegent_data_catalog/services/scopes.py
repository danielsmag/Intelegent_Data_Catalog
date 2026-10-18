"""Scope writes and reads."""

from uuid import UUID

from intelegent_data_catalog.exceptions import ConflictError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import ScopeRow, SpaceRow
from intelegent_data_catalog.registry.reads import scope_read
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import ScopeCreate, ScopeRead, ScopeUpdate
from intelegent_data_catalog.services.support import (
    apply_user_context,
    require_row,
    required,
    slug_after_patch,
    slug_for,
)


class ScopeService:
    """Scope persistence against the shared catalog session."""

    def __init__(self, repo: RegistryRepository) -> None:
        self._repo: RegistryRepository = repo

    def create_scope(self, space_id: UUID, body: ScopeCreate) -> ScopeRead:
        """Create a scope in a space."""
        self._space(space_id)
        slug: str = slug_for(body.name, body.slug)
        self._ensure_scope_slug(space_id, slug, None)
        row: ScopeRow = self._repo.add_scope(
            space_id=space_id,
            name=body.name,
            slug=slug,
            description=body.description,
        )
        self._repo.commit()
        return scope_read(row)

    def list_scopes(self, space_id: UUID) -> list[ScopeRead]:
        """List scopes in a space."""
        self._space(space_id)
        return [scope_read(row) for row in self._repo.list_scopes(space_id)]

    def get_scope(self, scope_id: UUID) -> ScopeRead:
        """Return one scope."""
        return scope_read(self._scope(scope_id))

    def update_scope(self, scope_id: UUID, patch: ScopeUpdate) -> ScopeRead:
        """Patch a scope. A new name refreshes the slug unless the patch includes one."""
        row: ScopeRow = self._scope(scope_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = required(patch.name, "name")
        next_slug: str | None = slug_after_patch(row.name, changes, patch.slug)
        if next_slug is not None:
            self._ensure_scope_slug(row.space_id, next_slug, row.id)
            row.slug = next_slug
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            apply_user_context(row, patch.context)
        row.updated_at = utc_now()
        self._repo.commit()
        return scope_read(row)

    def delete_scope(self, scope_id: UUID) -> None:
        """Delete a scope that has no tables. Bindings lose this default scope."""
        row: ScopeRow = self._scope(scope_id)
        if self._repo.count_tables_in_scope(scope_id) > 0:
            raise ConflictError("scope still has tables")
        self._repo.clear_default_scope(scope_id)
        self._repo.delete(row)
        self._repo.commit()

    def _ensure_scope_slug(self, space_id: UUID, slug: str, current_id: UUID | None) -> None:
        existing: ScopeRow | None = self._repo.find_scope_by_slug(space_id, slug)
        if existing is not None and existing.id != current_id:
            raise ConflictError(f"scope slug already exists: {slug}")

    def _space(self, space_id: UUID) -> SpaceRow:
        row: SpaceRow | None = self._repo.get_space(space_id)
        return require_row(row, "space", space_id)

    def _scope(self, scope_id: UUID) -> ScopeRow:
        row: ScopeRow | None = self._repo.get_scope(scope_id)
        return require_row(row, "scope", scope_id)
