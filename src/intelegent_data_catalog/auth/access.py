"""Request access: the caller and the active workspace."""

from dataclasses import dataclass
from uuid import UUID

from intelegent_data_catalog.auth.principal import Principal


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Visibility for one request. Workspace membership gates files and agents."""

    principal: Principal
    user_id: UUID
    workspace_id: UUID | None
    member_workspace_ids: frozenset[UUID]
    admin_workspace_ids: frozenset[UUID]

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
