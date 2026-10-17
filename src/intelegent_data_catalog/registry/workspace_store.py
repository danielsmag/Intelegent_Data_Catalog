"""Persistence for workspaces, members, bindings, and file nodes."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import (
    UserRow,
    WorkspaceMemberRow,
    WorkspaceNodeRow,
    WorkspaceRow,
    WorkspaceSpaceBindingRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository


class WorkspaceStore:
    """Reads and writes workspace rows in one session."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session
        self._repo: RegistryRepository = RegistryRepository(session)

    def add_workspace(
        self,
        *,
        name: str,
        slug: str,
        description: str | None,
    ) -> WorkspaceRow:
        """Insert a workspace."""
        now: datetime = utc_now()
        row: WorkspaceRow = WorkspaceRow(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
            description=description,
            is_system=False,
            created_at=now,
            updated_at=now,
        )
        return self._repo._add(row)

    def list_workspaces(self) -> list[WorkspaceRow]:
        """Return every workspace in name order."""
        return list(self._session.scalars(select(WorkspaceRow).order_by(WorkspaceRow.name)))

    def get_workspace(self, workspace_id: uuid.UUID) -> WorkspaceRow | None:
        """Return one workspace or none."""
        return self._session.get(WorkspaceRow, workspace_id)

    def find_workspace_by_slug(self, slug: str) -> WorkspaceRow | None:
        """Return the workspace that owns a slug."""
        return self._session.scalar(select(WorkspaceRow).where(WorkspaceRow.slug == slug))

    def count_bindings(self, workspace_id: uuid.UUID) -> int:
        """Count spaces bound to a workspace."""
        counted: int | None = self._session.scalar(
            select(func.count())
            .select_from(WorkspaceSpaceBindingRow)
            .where(WorkspaceSpaceBindingRow.workspace_id == workspace_id)
        )
        if counted is None:
            return 0
        return counted

    def list_bound_space_ids(self, workspace_id: uuid.UUID) -> list[uuid.UUID]:
        """Return space ids bound to a workspace."""
        return list(
            self._session.scalars(
                select(WorkspaceSpaceBindingRow.space_id).where(
                    WorkspaceSpaceBindingRow.workspace_id == workspace_id
                )
            )
        )

    def find_binding(
        self,
        workspace_id: uuid.UUID,
        space_id: uuid.UUID,
    ) -> WorkspaceSpaceBindingRow | None:
        """Return one workspace-space binding."""
        return self._session.scalar(
            select(WorkspaceSpaceBindingRow).where(
                WorkspaceSpaceBindingRow.workspace_id == workspace_id,
                WorkspaceSpaceBindingRow.space_id == space_id,
            )
        )

    def add_binding(self, workspace_id: uuid.UUID, space_id: uuid.UUID) -> WorkspaceSpaceBindingRow:
        """Insert a workspace-space binding."""
        row: WorkspaceSpaceBindingRow = WorkspaceSpaceBindingRow(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            space_id=space_id,
            created_at=utc_now(),
        )
        return self._repo._add(row)

    def list_members(self, workspace_id: uuid.UUID) -> list[tuple[WorkspaceMemberRow, UserRow]]:
        """Return members with their user rows."""
        statement = (
            select(WorkspaceMemberRow, UserRow)
            .join(UserRow, WorkspaceMemberRow.user_id == UserRow.id)
            .where(WorkspaceMemberRow.workspace_id == workspace_id)
            .order_by(UserRow.display_name)
        )
        return list(self._session.execute(statement).all())

    def find_member(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> WorkspaceMemberRow | None:
        """Return one membership."""
        return self._session.scalar(
            select(WorkspaceMemberRow).where(
                WorkspaceMemberRow.workspace_id == workspace_id,
                WorkspaceMemberRow.user_id == user_id,
            )
        )

    def find_user_by_email(self, email: str) -> UserRow | None:
        """Return a user by email. Comparison is case-sensitive."""
        return self._session.scalar(select(UserRow).where(UserRow.email == email))

    def get_user(self, user_id: uuid.UUID) -> UserRow | None:
        """Return one user or none."""
        return self._session.get(UserRow, user_id)

    def add_member(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
    ) -> WorkspaceMemberRow:
        """Insert a membership."""
        row: WorkspaceMemberRow = WorkspaceMemberRow(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            created_at=utc_now(),
        )
        return self._repo._add(row)

    def object_keys(self, workspace_id: uuid.UUID) -> list[str]:
        """Return object keys for files in a workspace."""
        rows: list[str | None] = list(
            self._session.scalars(
                select(WorkspaceNodeRow.object_key).where(
                    WorkspaceNodeRow.workspace_id == workspace_id,
                    WorkspaceNodeRow.object_key.is_not(None),
                )
            )
        )
        keys: list[str] = []
        item: str | None
        for item in rows:
            if item is not None:
                keys.append(item)
        return keys

    def delete(self, row: object) -> None:
        """Delete a row already loaded in this session."""
        self._repo.delete(row)

    def commit(self) -> None:
        """Commit the current transaction."""
        self._repo.commit()
