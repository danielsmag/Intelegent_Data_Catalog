"""Workspace folder and file tree. Bytes go to the object store."""

import mimetypes
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.sync import AssetSync
from intelegent_data_catalog.assets.triggers import AssetChangeTrigger
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.exceptions import (
    EntityNotFoundError,
    PayloadTooLargeError,
    ValidationAppError,
)
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import WorkspaceNodeKind
from intelegent_data_catalog.persistence.models import AssetRow, WorkspaceNodeRow
from intelegent_data_catalog.registry.schemas import (
    FolderCreate,
    NodeUpdate,
    TextFileCreate,
    WorkspaceNodeRead,
)
from intelegent_data_catalog.registry.workspace_store import WorkspaceStore
from intelegent_data_catalog.storage.object_store import ObjectStore

_SHARED_ROOT: str = "Shared"
_USERS_ROOT: str = "Users"


class WorkspaceFileService:
    """Folders and files under Shared and Users/<subject>."""

    def __init__(
        self,
        session: Session,
        objects: ObjectStore,
        max_upload_bytes: int,
        changes: AssetChangeTrigger | None = None,
    ) -> None:
        self._session: Session = session
        self._store: WorkspaceStore = WorkspaceStore(session)
        self._objects: ObjectStore = objects
        self._max_upload_bytes: int = max_upload_bytes
        self._changes: AssetChangeTrigger | None = changes

    def list_nodes(
        self,
        workspace_id: UUID,
        parent_id: UUID | None,
        access: AccessContext,
    ) -> list[WorkspaceNodeRead]:
        """List one folder. The first call creates Shared and Users/<caller>."""
        self._require_workspace(workspace_id, access)
        if parent_id is None:
            self._ensure_roots(workspace_id, access)
            self._store.commit()
        else:
            parent: WorkspaceNodeRow = self._visible_folder(workspace_id, parent_id, access)
            if parent.path == _USERS_ROOT and not access.principal.is_admin:
                own: WorkspaceNodeRow | None = self._by_path(
                    workspace_id,
                    f"{_USERS_ROOT}/{_safe_subject(access.principal.subject)}",
                )
                if own is None:
                    return []
                return [self._read(own)]
        children: list[WorkspaceNodeRow] = self._children(workspace_id, parent_id)
        visible: list[WorkspaceNodeRead] = []
        child: WorkspaceNodeRow
        for child in children:
            if self._can_see(child, access):
                visible.append(self._read(child))
        return visible

    def create_folder(
        self,
        workspace_id: UUID,
        body: FolderCreate,
        access: AccessContext,
    ) -> WorkspaceNodeRead:
        """Create a folder under an existing folder."""
        self._require_workspace(workspace_id, access)
        parent: WorkspaceNodeRow = self._visible_folder(workspace_id, body.parent_id, access)
        self._reject_users_root(parent)
        name: str = _node_name(body.name)
        row: WorkspaceNodeRow = self._insert_folder(workspace_id, parent, name, access)
        self._store.commit()
        return self._read(row)

    def create_text_file(
        self,
        workspace_id: UUID,
        body: TextFileCreate,
        access: AccessContext,
    ) -> WorkspaceNodeRead:
        """Create a text file and store its UTF-8 bytes."""
        payload: bytes = body.text.encode("utf-8")
        return self._create_file(
            workspace_id,
            body.parent_id,
            body.name,
            payload,
            _content_type(body.name, "text/plain"),
            access,
        )

    def upload_file(
        self,
        workspace_id: UUID,
        parent_id: UUID,
        name: str,
        payload: bytes,
        content_type: str | None,
        access: AccessContext,
    ) -> WorkspaceNodeRead:
        """Store an uploaded file under a folder."""
        stored_type: str = _content_type(name, content_type)
        return self._create_file(workspace_id, parent_id, name, payload, stored_type, access)

    def get_node(
        self,
        workspace_id: UUID,
        node_id: UUID,
        access: AccessContext,
    ) -> WorkspaceNodeRead:
        """Return one visible node."""
        self._require_workspace(workspace_id, access)
        return self._read(self._visible_node(workspace_id, node_id, access))

    def require_visible_file(
        self,
        workspace_id: UUID,
        node_id: UUID,
        access: AccessContext,
    ) -> WorkspaceNodeRow:
        """Return a file the caller can see. Folders are reported as missing notebooks."""
        self._require_workspace(workspace_id, access)
        node: WorkspaceNodeRow = self._visible_node(workspace_id, node_id, access)
        if node.kind != WorkspaceNodeKind.FILE.value:
            raise EntityNotFoundError("notebook", str(node_id))
        return node

    def read_content(
        self,
        workspace_id: UUID,
        node_id: UUID,
        access: AccessContext,
    ) -> tuple[WorkspaceNodeRow, bytes]:
        """Return a file row and its bytes."""
        self._require_workspace(workspace_id, access)
        node: WorkspaceNodeRow = self._visible_node(workspace_id, node_id, access)
        if node.kind != WorkspaceNodeKind.FILE.value or node.object_key is None:
            raise ValidationAppError("node is not a file", field="node_id")
        return node, self._objects.get(node.object_key)

    def replace_content(
        self,
        workspace_id: UUID,
        node_id: UUID,
        payload: bytes,
        access: AccessContext,
    ) -> WorkspaceNodeRead:
        """Replace the bytes of a file."""
        self._require_workspace(workspace_id, access)
        self._check_size(payload)
        node: WorkspaceNodeRow = self._visible_node(workspace_id, node_id, access)
        if node.kind != WorkspaceNodeKind.FILE.value or node.object_key is None:
            raise ValidationAppError("node is not a file", field="node_id")
        content_type: str = node.content_type or "application/octet-stream"
        self._objects.put(node.object_key, payload, content_type)
        node.size_bytes = len(payload)
        node.updated_at = utc_now()
        asset_id: UUID | None = self._touch_file(node)
        self._store.commit()
        self._notify_change(asset_id)
        return self._read(node)

    def update_node(
        self,
        workspace_id: UUID,
        node_id: UUID,
        patch: NodeUpdate,
        access: AccessContext,
    ) -> WorkspaceNodeRead:
        """Rename a node or move it under another folder."""
        self._require_workspace(workspace_id, access)
        node: WorkspaceNodeRow = self._visible_node(workspace_id, node_id, access)
        self._reject_root(node)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        parent: WorkspaceNodeRow = self._parent_of(node)
        if "parent_id" in changes:
            if patch.parent_id is None:
                raise ValidationAppError("a file or folder needs a parent", field="parent_id")
            parent = self._visible_folder(workspace_id, patch.parent_id, access)
            self._reject_users_root(parent)
            moved_inside: bool = parent.id == node.id or parent.path == node.path
            if moved_inside or parent.path.startswith(f"{node.path}/"):
                raise ValidationAppError("cannot move a folder into itself", field="parent_id")
        name: str = node.name
        if "name" in changes and patch.name is not None:
            name = _node_name(patch.name)
        self._ensure_unique(workspace_id, parent.id, name, node.id)
        old_path: str = node.path
        new_path: str = f"{parent.path}/{name}"
        node.name = name
        node.parent_id = parent.id
        node.path = new_path
        node.owner_user_id = parent.owner_user_id
        node.updated_at = utc_now()
        if node.kind == WorkspaceNodeKind.FOLDER.value:
            self._rewrite_descendants(node, old_path, new_path, parent.owner_user_id)
        elif node.kind == WorkspaceNodeKind.FILE.value:
            self._touch_file(node)
        self._store.commit()
        return self._read(node)

    def delete_node(self, workspace_id: UUID, node_id: UUID, access: AccessContext) -> None:
        """Delete a node, its descendants, and their objects."""
        self._require_workspace(workspace_id, access)
        node: WorkspaceNodeRow = self._visible_node(workspace_id, node_id, access)
        self._reject_root(node)
        if node.parent_id is not None:
            parent: WorkspaceNodeRow | None = self._session.get(WorkspaceNodeRow, node.parent_id)
            if parent is not None and parent.path == _USERS_ROOT:
                raise ValidationAppError("cannot delete a user root", field="node_id")
        keys: list[str] = []
        item: WorkspaceNodeRow
        for item in self._subtree(node):
            if item.object_key is not None:
                keys.append(item.object_key)
            sync: AssetSync | None = AssetSync.for_row(item)
            if sync is not None:
                sync.delete_file(item)
        self._store.delete(node)
        self._store.commit()
        key: str
        for key in keys:
            self._objects.delete(key)

    def _create_file(
        self,
        workspace_id: UUID,
        parent_id: UUID,
        raw_name: str,
        payload: bytes,
        content_type: str,
        access: AccessContext,
    ) -> WorkspaceNodeRead:
        self._require_workspace(workspace_id, access)
        self._check_size(payload)
        parent: WorkspaceNodeRow = self._visible_folder(workspace_id, parent_id, access)
        self._reject_users_root(parent)
        name: str = _node_name(raw_name)
        self._ensure_unique(workspace_id, parent.id, name, None)
        now = utc_now()
        node_id: UUID = uuid4()
        object_key: str = f"{workspace_id}/{node_id}"
        row: WorkspaceNodeRow = WorkspaceNodeRow(
            id=node_id,
            workspace_id=workspace_id,
            parent_id=parent.id,
            owner_user_id=parent.owner_user_id,
            created_by=access.user_id,
            kind=WorkspaceNodeKind.FILE.value,
            name=name,
            path=f"{parent.path}/{name}",
            content_type=content_type,
            size_bytes=len(payload),
            object_key=object_key,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        self._objects.put(object_key, payload, content_type)
        self._touch_file(row)
        self._store.commit()
        return self._read(row)

    def _touch_file(self, row: WorkspaceNodeRow) -> UUID | None:
        sync: AssetSync | None = AssetSync.for_row(row)
        if sync is None:
            return None
        asset: AssetRow | None = sync.ensure_file(row)
        if asset is None:
            return None
        return asset.id

    def _notify_change(self, asset_id: UUID | None) -> None:
        if asset_id is None or self._changes is None:
            return
        self._changes.changed(asset_id)

    def _ensure_roots(self, workspace_id: UUID, access: AccessContext) -> None:
        shared: WorkspaceNodeRow = self._ensure_folder(
            workspace_id,
            None,
            _SHARED_ROOT,
            _SHARED_ROOT,
            None,
            access.user_id,
        )
        users: WorkspaceNodeRow = self._ensure_folder(
            workspace_id,
            None,
            _USERS_ROOT,
            _USERS_ROOT,
            None,
            access.user_id,
        )
        subject: str = _safe_subject(access.principal.subject)
        self._ensure_folder(
            workspace_id,
            users.id,
            subject,
            f"{_USERS_ROOT}/{subject}",
            access.user_id,
            access.user_id,
        )
        del shared

    def _ensure_folder(
        self,
        workspace_id: UUID,
        parent_id: UUID | None,
        name: str,
        path: str,
        owner_user_id: UUID | None,
        created_by: UUID,
    ) -> WorkspaceNodeRow:
        existing: WorkspaceNodeRow | None = self._by_path(workspace_id, path)
        if existing is not None:
            return existing
        now = utc_now()
        row: WorkspaceNodeRow = WorkspaceNodeRow(
            id=uuid4(),
            workspace_id=workspace_id,
            parent_id=parent_id,
            owner_user_id=owner_user_id,
            created_by=created_by,
            kind=WorkspaceNodeKind.FOLDER.value,
            name=name,
            path=path,
            content_type=None,
            size_bytes=None,
            object_key=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def _insert_folder(
        self,
        workspace_id: UUID,
        parent: WorkspaceNodeRow,
        name: str,
        access: AccessContext,
    ) -> WorkspaceNodeRow:
        self._ensure_unique(workspace_id, parent.id, name, None)
        return self._ensure_folder(
            workspace_id,
            parent.id,
            name,
            f"{parent.path}/{name}",
            parent.owner_user_id,
            access.user_id,
        )

    def _rewrite_descendants(
        self,
        folder: WorkspaceNodeRow,
        old_path: str,
        new_path: str,
        owner_user_id: UUID | None,
    ) -> None:
        child: WorkspaceNodeRow
        for child in self._subtree(folder):
            if child.id == folder.id:
                continue
            suffix: str = child.path[len(old_path) :]
            child.path = f"{new_path}{suffix}"
            child.owner_user_id = owner_user_id
            child.updated_at = utc_now()

    def _subtree(self, node: WorkspaceNodeRow) -> list[WorkspaceNodeRow]:
        found: list[WorkspaceNodeRow] = [node]
        child: WorkspaceNodeRow
        for child in self._children(node.workspace_id, node.id):
            found.extend(self._subtree(child))
        return found

    def _children(self, workspace_id: UUID, parent_id: UUID | None) -> list[WorkspaceNodeRow]:
        statement = select(WorkspaceNodeRow).where(WorkspaceNodeRow.workspace_id == workspace_id)
        if parent_id is None:
            statement = statement.where(WorkspaceNodeRow.parent_id.is_(None))
        else:
            statement = statement.where(WorkspaceNodeRow.parent_id == parent_id)
        statement = statement.order_by(WorkspaceNodeRow.kind, WorkspaceNodeRow.name)
        return list(self._session.scalars(statement))

    def _by_path(self, workspace_id: UUID, path: str) -> WorkspaceNodeRow | None:
        return self._session.scalar(
            select(WorkspaceNodeRow).where(
                WorkspaceNodeRow.workspace_id == workspace_id,
                WorkspaceNodeRow.path == path,
            )
        )

    def _parent_of(self, node: WorkspaceNodeRow) -> WorkspaceNodeRow:
        if node.parent_id is None:
            raise ValidationAppError("cannot change a root folder", field="node_id")
        parent: WorkspaceNodeRow | None = self._session.get(WorkspaceNodeRow, node.parent_id)
        if parent is None:
            raise EntityNotFoundError("folder", str(node.parent_id))
        return parent

    def _visible_folder(
        self,
        workspace_id: UUID,
        node_id: UUID,
        access: AccessContext,
    ) -> WorkspaceNodeRow:
        node: WorkspaceNodeRow = self._visible_node(workspace_id, node_id, access)
        if node.kind != WorkspaceNodeKind.FOLDER.value:
            raise ValidationAppError("parent is not a folder", field="parent_id")
        return node

    def _visible_node(
        self,
        workspace_id: UUID,
        node_id: UUID,
        access: AccessContext,
    ) -> WorkspaceNodeRow:
        node: WorkspaceNodeRow | None = self._session.get(WorkspaceNodeRow, node_id)
        if node is None or node.workspace_id != workspace_id or not self._can_see(node, access):
            raise EntityNotFoundError("node", str(node_id))
        return node

    def _can_see(self, node: WorkspaceNodeRow, access: AccessContext) -> bool:
        if node.owner_user_id is None or access.principal.is_admin:
            return True
        return node.owner_user_id == access.user_id

    def _require_workspace(self, workspace_id: UUID, access: AccessContext) -> None:
        if self._store.get_workspace(workspace_id) is None or not access.is_member(workspace_id):
            raise EntityNotFoundError("workspace", str(workspace_id))

    def _ensure_unique(
        self,
        workspace_id: UUID,
        parent_id: UUID,
        name: str,
        ignore_id: UUID | None,
    ) -> None:
        child: WorkspaceNodeRow
        for child in self._children(workspace_id, parent_id):
            if child.name == name and child.id != ignore_id:
                raise ValidationAppError("name already exists in this folder", field="name")

    def _check_size(self, payload: bytes) -> None:
        if len(payload) > self._max_upload_bytes:
            raise PayloadTooLargeError("upload is too large")

    def _read(self, row: WorkspaceNodeRow) -> WorkspaceNodeRead:
        return WorkspaceNodeRead(
            id=row.id,
            workspace_id=row.workspace_id,
            parent_id=row.parent_id,
            name=row.name,
            path=row.path,
            kind=WorkspaceNodeKind(row.kind),
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            owner_user_id=row.owner_user_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _reject_users_root(self, parent: WorkspaceNodeRow) -> None:
        if parent.path == _USERS_ROOT:
            raise ValidationAppError("create files inside your user folder", field="parent_id")

    def _reject_root(self, node: WorkspaceNodeRow) -> None:
        if node.parent_id is None or node.path == _SHARED_ROOT or node.path == _USERS_ROOT:
            raise ValidationAppError("cannot change a root folder", field="node_id")
        if node.parent_id is not None:
            parent: WorkspaceNodeRow | None = self._session.get(WorkspaceNodeRow, node.parent_id)
            if parent is not None and parent.path == _USERS_ROOT:
                raise ValidationAppError("cannot change a user root", field="node_id")


def _node_name(name: str) -> str:
    cleaned: str = name.strip()
    if cleaned in {"", ".", ".."} or "/" in cleaned or "\\" in cleaned or len(cleaned) > 255:
        raise ValidationAppError("invalid file name", field="name")
    return cleaned


def _safe_subject(subject: str) -> str:
    cleaned: str = subject.replace("/", "_").replace("\\", "_").strip()
    if cleaned in {"", ".", ".."}:
        return "user"
    return cleaned[:255]


def _content_type(name: str, provided: str | None) -> str:
    if provided is not None and provided.strip() != "" and provided != "application/octet-stream":
        return provided.split(";", 1)[0].strip()
    guessed: str | None
    guessed, _encoding = mimetypes.guess_type(name)
    if guessed is None:
        return "application/octet-stream"
    return guessed

