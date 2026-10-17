"""Workspace, membership, and space-binding use cases."""

from uuid import UUID

from sqlalchemy.orm import Session

from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.exceptions import (
    AuthorizationError,
    ConflictError,
    EntityNotFoundError,
    ValidationAppError,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_SLUG
from intelegent_data_catalog.persistence.enums import WorkspaceRole
from intelegent_data_catalog.persistence.models import (
    SpaceRow,
    UserRow,
    WorkspaceMemberRow,
    WorkspaceRow,
    WorkspaceSpaceBindingRow,
)
from intelegent_data_catalog.registry.identifiers import slugify
from intelegent_data_catalog.registry.repository import RegistryRepository
from intelegent_data_catalog.registry.schemas import (
    MembershipRead,
    MeRead,
    WorkspaceCreate,
    WorkspaceMemberCreate,
    WorkspaceMemberRead,
    WorkspaceRead,
    WorkspaceUpdate,
)
from intelegent_data_catalog.registry.workspace_store import WorkspaceStore
from intelegent_data_catalog.storage.object_store import ObjectStore


class WorkspaceService:
    """Workspace administration against PostgreSQL."""

    def __init__(self, session: Session, objects: ObjectStore) -> None:
        self._store: WorkspaceStore = WorkspaceStore(session)
        self._registry: RegistryRepository = RegistryRepository(session)
        self._objects: ObjectStore = objects

    def me(self, access: AccessContext) -> MeRead:
        """Return the caller and the workspaces they can open."""
        return MeRead(
            subject=access.principal.subject,
            email=access.principal.email,
            name=access.principal.name,
            user_id=access.user_id,
            is_admin=access.principal.is_admin,
            workspace_id=access.workspace_id,
            memberships=self._memberships(access),
        )

    def list_workspaces(self, access: AccessContext) -> list[WorkspaceRead]:
        """List workspaces the caller can open. Platform admins see every workspace."""
        rows: list[WorkspaceRead] = []
        row: WorkspaceRow
        for row in self._store.list_workspaces():
            if access.principal.is_admin or row.id in access.member_workspace_ids:
                rows.append(self._read(row, access))
        return rows

    def create_workspace(self, body: WorkspaceCreate, access: AccessContext) -> WorkspaceRead:
        """Create a workspace. Platform admins only."""
        self._require_platform_admin(access)
        slug: str = _slug_for(body.name, body.slug)
        if slug == SHARED_WORKSPACE_SLUG or self._store.find_workspace_by_slug(slug) is not None:
            raise ConflictError("workspace slug already exists")
        row: WorkspaceRow = self._store.add_workspace(
            name=body.name,
            slug=slug,
            description=body.description,
        )
        self._store.add_member(row.id, access.user_id, WorkspaceRole.ADMIN.value)
        self._store.commit()
        return self._read(row, access)

    def get_workspace(self, workspace_id: UUID, access: AccessContext) -> WorkspaceRead:
        """Return one workspace the caller can open."""
        return self._read(self._visible_workspace(workspace_id, access), access)

    def update_workspace(
        self,
        workspace_id: UUID,
        patch: WorkspaceUpdate,
        access: AccessContext,
    ) -> WorkspaceRead:
        """Patch a workspace. Workspace admins only. The shared workspace stays."""
        row: WorkspaceRow = self._managed_workspace(workspace_id, access)
        if row.is_system:
            raise ConflictError("shared workspace cannot be renamed")
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes and patch.name is not None:
            row.name = patch.name
        next_slug: str | None = None
        if "slug" in changes:
            next_slug = _slug_for(row.name, patch.slug)
        elif "name" in changes:
            next_slug = _slug_for(row.name, None)
        if next_slug is not None:
            taken: WorkspaceRow | None = self._store.find_workspace_by_slug(next_slug)
            if taken is not None and taken.id != row.id:
                raise ConflictError("workspace slug already exists")
            if next_slug == SHARED_WORKSPACE_SLUG and not row.is_system:
                raise ConflictError("workspace slug already exists")
            row.slug = next_slug
        if "description" in changes:
            row.description = patch.description
        row.updated_at = utc_now()
        self._store.commit()
        return self._read(row, access)

    def delete_workspace(self, workspace_id: UUID, access: AccessContext) -> None:
        """Delete a workspace that has no space bindings."""
        row: WorkspaceRow = self._managed_workspace(workspace_id, access)
        if row.is_system:
            raise ConflictError("shared workspace cannot be deleted")
        if self._store.count_bindings(workspace_id) > 0:
            raise ConflictError("workspace still has bound spaces")
        keys: list[str] = self._store.object_keys(workspace_id)
        self._store.delete(row)
        self._store.commit()
        key: str
        for key in keys:
            self._objects.delete(key)

    def list_members(self, workspace_id: UUID, access: AccessContext) -> list[WorkspaceMemberRead]:
        """List members. The shared workspace has no stored member rows."""
        self._visible_workspace(workspace_id, access)
        found: list[WorkspaceMemberRead] = []
        pair: tuple[WorkspaceMemberRow, UserRow]
        for pair in self._store.list_members(workspace_id):
            member: WorkspaceMemberRow = pair[0]
            user: UserRow = pair[1]
            found.append(
                WorkspaceMemberRead(
                    user_id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    role=WorkspaceRole(member.role),
                    created_at=member.created_at,
                )
            )
        return found

    def add_member(
        self,
        workspace_id: UUID,
        body: WorkspaceMemberCreate,
        access: AccessContext,
    ) -> WorkspaceMemberRead:
        """Add a user who has signed in at least once."""
        row: WorkspaceRow = self._managed_workspace(workspace_id, access)
        if row.is_system:
            raise ConflictError("shared workspace includes every user")
        user: UserRow | None = self._store.find_user_by_email(body.email.strip())
        if user is None:
            raise EntityNotFoundError("user", body.email)
        if self._store.find_member(workspace_id, user.id) is not None:
            raise ConflictError("user is already a member")
        member: WorkspaceMemberRow = self._store.add_member(
            workspace_id,
            user.id,
            body.role.value,
        )
        self._store.commit()
        return WorkspaceMemberRead(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=WorkspaceRole(member.role),
            created_at=member.created_at,
        )

    def remove_member(self, workspace_id: UUID, user_id: UUID, access: AccessContext) -> None:
        """Remove a member. The shared workspace has no stored members."""
        row: WorkspaceRow = self._managed_workspace(workspace_id, access)
        if row.is_system:
            raise ConflictError("shared workspace includes every user")
        member: WorkspaceMemberRow | None = self._store.find_member(workspace_id, user_id)
        if member is None:
            raise EntityNotFoundError("member", str(user_id))
        self._store.delete(member)
        self._store.commit()

    def list_bound_spaces(self, workspace_id: UUID, access: AccessContext) -> list[UUID]:
        """Return space ids bound to a workspace the caller can open."""
        self._visible_workspace(workspace_id, access)
        return self._store.list_bound_space_ids(workspace_id)

    def bind_space(self, workspace_id: UUID, space_id: UUID, access: AccessContext) -> None:
        """Bind a visible space to a workspace."""
        self._managed_workspace(workspace_id, access)
        space: SpaceRow | None = self._registry.get_space(space_id)
        if space is None or not access.allows_space(space_id):
            raise EntityNotFoundError("space", str(space_id))
        if self._store.find_binding(workspace_id, space_id) is not None:
            return
        self._store.add_binding(workspace_id, space_id)
        self._store.commit()

    def unbind_space(self, workspace_id: UUID, space_id: UUID, access: AccessContext) -> None:
        """Remove a space binding."""
        self._managed_workspace(workspace_id, access)
        binding: WorkspaceSpaceBindingRow | None = self._store.find_binding(workspace_id, space_id)
        if binding is None:
            raise EntityNotFoundError("binding", str(space_id))
        self._store.delete(binding)
        self._store.commit()

    def _memberships(self, access: AccessContext) -> list[MembershipRead]:
        found: list[MembershipRead] = []
        row: WorkspaceRow
        for row in self._store.list_workspaces():
            included: bool = access.principal.is_admin or row.id in access.member_workspace_ids
            if not included:
                continue
            role: WorkspaceRole = WorkspaceRole.MEMBER
            if access.is_workspace_admin(row.id):
                role = WorkspaceRole.ADMIN
            found.append(
                MembershipRead(
                    workspace_id=row.id,
                    name=row.name,
                    slug=row.slug,
                    role=role,
                    is_system=row.is_system,
                )
            )
        return found

    def _read(self, row: WorkspaceRow, access: AccessContext) -> WorkspaceRead:
        role: WorkspaceRole | None = None
        if access.principal.is_admin or row.id in access.member_workspace_ids:
            role = (
                WorkspaceRole.ADMIN
                if access.is_workspace_admin(row.id)
                else WorkspaceRole.MEMBER
            )
        return WorkspaceRead(
            id=row.id,
            name=row.name,
            slug=row.slug,
            description=row.description,
            is_system=row.is_system,
            role=role,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _visible_workspace(self, workspace_id: UUID, access: AccessContext) -> WorkspaceRow:
        row: WorkspaceRow | None = self._store.get_workspace(workspace_id)
        if row is None or not access.is_member(workspace_id):
            raise EntityNotFoundError("workspace", str(workspace_id))
        return row

    def _managed_workspace(self, workspace_id: UUID, access: AccessContext) -> WorkspaceRow:
        row: WorkspaceRow = self._visible_workspace(workspace_id, access)
        if not access.is_workspace_admin(workspace_id):
            raise AuthorizationError("workspace admin required")
        return row

    def _require_platform_admin(self, access: AccessContext) -> None:
        if not access.principal.is_admin:
            raise AuthorizationError("platform admin required")


def _slug_for(name: str, explicit: str | None) -> str:
    try:
        if explicit is not None:
            return explicit
        return slugify(name)
    except ValueError as err:
        raise ValidationAppError(str(err), field="slug") from err
