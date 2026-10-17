"""Request access: the caller, the active workspace, and visible spaces."""

from dataclasses import dataclass
from uuid import UUID

from intelegent_data_catalog.auth.principal import Principal


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Visibility for one request. ``visible_space_ids`` is ``None`` when unrestricted."""

    principal: Principal
    user_id: UUID
    workspace_id: UUID | None
    member_workspace_ids: frozenset[UUID]
    admin_workspace_ids: frozenset[UUID]
    visible_space_ids: frozenset[UUID] | None

    def allows_space(self, space_id: UUID) -> bool:
        """Return whether this caller may see ``space_id``."""
        if self.visible_space_ids is None:
            return True
        return space_id in self.visible_space_ids

    def is_member(self, workspace_id: UUID) -> bool:
        """Return whether this caller may use ``workspace_id``."""
        if self.principal.is_admin:
            return True
        return workspace_id in self.member_workspace_ids

    def is_workspace_admin(self, workspace_id: UUID) -> bool:
        """Return whether this caller manages ``workspace_id``."""
        if self.principal.is_admin:
            return True
        return workspace_id in self.admin_workspace_ids
