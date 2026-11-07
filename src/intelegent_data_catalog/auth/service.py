"""Resolve a principal into a user row and workspace memberships."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.exceptions import (
    AuthorizationError,
    EntityNotFoundError,
    ValidationAppError,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.enums import WorkspaceRole
from intelegent_data_catalog.persistence.models import (
    UserRow,
    WorkspaceMemberRow,
    WorkspaceRow,
)


class AccessService:
    """Upsert the caller and load workspace memberships."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def resolve(self, principal: Principal, workspace_header: str | None) -> AccessContext:
        """Persist the user and check the workspace header."""
        user: UserRow = self._upsert(principal)
        self._session.commit()
        workspace_id: UUID | None = self._workspace_id(workspace_header, principal, user.id)
        memberships: list[WorkspaceMemberRow] = self._memberships(user.id)
        member_ids: set[UUID] = {row.workspace_id for row in memberships}
        member_ids.add(SHARED_WORKSPACE_ID)
        admin_ids: set[UUID] = {
            row.workspace_id for row in memberships if row.role == WorkspaceRole.ADMIN.value
        }
        if principal.is_admin:
            admin_ids.add(SHARED_WORKSPACE_ID)
        return AccessContext(
            principal=principal,
            user_id=user.id,
            workspace_id=workspace_id,
            member_workspace_ids=frozenset(member_ids),
            admin_workspace_ids=frozenset(admin_ids),
        )

    def _upsert(self, principal: Principal) -> UserRow:
        row: UserRow | None = self._session.scalar(
            select(UserRow).where(UserRow.subject == principal.subject)
        )
        now = utc_now()
        if row is None:
            row = UserRow(
                id=uuid4(),
                subject=principal.subject,
                email=principal.email,
                display_name=principal.name[:255],
                last_login_at=now,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
            self._session.flush()
            return row
        row.email = principal.email
        row.display_name = principal.name[:255]
        row.last_login_at = now
        row.updated_at = now
        self._session.flush()
        return row

    def _workspace_id(self, header: str | None, principal: Principal, user_id: UUID) -> UUID | None:
        if header is None or header.strip() == "":
            return None
        try:
            workspace_id: UUID = UUID(header.strip())
        except ValueError as err:
            raise ValidationAppError(
                "X-Workspace-Id must be a UUID",
                field="X-Workspace-Id",
            ) from err
        row: WorkspaceRow | None = self._session.get(WorkspaceRow, workspace_id)
        if row is None:
            raise EntityNotFoundError("workspace", str(workspace_id))
        if principal.is_admin or row.is_system:
            return workspace_id
        member: WorkspaceMemberRow | None = self._session.scalar(
            select(WorkspaceMemberRow).where(
                WorkspaceMemberRow.workspace_id == workspace_id,
                WorkspaceMemberRow.user_id == user_id,
            )
        )
        if member is None:
            raise AuthorizationError("not a member of this workspace")
        return workspace_id

    def _memberships(self, user_id: UUID) -> list[WorkspaceMemberRow]:
        return list(
            self._session.scalars(
                select(WorkspaceMemberRow).where(WorkspaceMemberRow.user_id == user_id)
            )
        )

