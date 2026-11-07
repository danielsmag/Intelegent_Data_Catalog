"""Agent notebooks on the definition object store.

One store is rooted at ``agent-memory/``. The harness namespace is the agent id,
so a note lives at ``agent-memory/{agent_id}/main/MEMORY.md``.
"""

import re
from collections.abc import Mapping

import anyio
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_ai_harness.memory._store import (
    MemoryConflictError,
    MemoryFile,
    MemoryMutation,
    MemoryOperation,
    MemoryOperationConflictError,
    MemoryPathEscapeError,
    MemorySearchResult,
    decode_metadata,
    encode_metadata,
    is_direct_child,
    lexical_search,
    validate_store_path,
    validate_store_prefix,
)

from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.storage.object_store import (
    ObjectStore,
    PreconditionFailed,
    StoredBody,
    StoredObject,
)

_RECEIPT_ID: re.Pattern[str] = re.compile(r"[A-Za-z0-9_-]{1,200}")


class _Receipt(BaseModel):
    """One replay record. It sits beside the markdown, not inside it."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    version: str | None
    existed: bool


class ObjectMemoryStore:
    """Harness memory backed by the definition object store."""

    def __init__(self, objects: ObjectStore, root: str) -> None:
        prefix: str = root if root.endswith("/") else f"{root}/"
        self._objects: ObjectStore = objects
        self._root: str = prefix

    async def read(self, path: str, *, max_chars: int) -> MemoryFile | None:
        """Return the file at ``path``, or none when it is absent."""
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        key: str = self._key(path)
        try:
            stored: StoredBody = await anyio.to_thread.run_sync(self._objects.read_object, key)
        except EntityNotFoundError:
            return None
        text: str = stored.body.decode("utf-8")
        return MemoryFile(
            content=text[:max_chars],
            version=stored.etag,
            operation_id=stored.metadata.get("operation-id"),
            truncated=len(text) > max_chars,
            metadata=decode_metadata(stored.metadata.get("write-metadata")),
        )

    async def get_operation(self, operation: MemoryOperation) -> MemoryMutation | None:
        """Return a prior result for ``operation``, checking its fingerprint."""
        key: str = self._receipt_key(operation.id)
        try:
            raw: bytes = await anyio.to_thread.run_sync(self._objects.get, key)
        except EntityNotFoundError:
            return None
        try:
            receipt: _Receipt = _Receipt.model_validate_json(raw)
        except ValidationError as err:
            raise MemoryOperationConflictError(
                f"operation id {operation.id!r} has a corrupt receipt",
            ) from err
        if receipt.fingerprint != operation.fingerprint:
            raise MemoryOperationConflictError(
                f"operation id {operation.id!r} was reused with different arguments",
            )
        return MemoryMutation(version=receipt.version, replayed=True, existed=receipt.existed)

    async def write(
        self,
        path: str,
        content: str,
        *,
        expected_version: str | None,
        operation: MemoryOperation | None = None,
    ) -> MemoryMutation:
        """Store the body and record no ``updated_by``."""
        return await self.write_with_metadata(
            path,
            content,
            expected_version=expected_version,
            metadata=None,
            operation=operation,
        )

    async def write_with_metadata(
        self,
        path: str,
        content: str,
        *,
        expected_version: str | None,
        metadata: Mapping[str, str] | None,
        operation: MemoryOperation | None = None,
    ) -> MemoryMutation:
        """Replace one file when ``expected_version`` is still current."""
        key: str = self._key(path)
        if operation is not None:
            previous: MemoryMutation | None = await self.get_operation(operation)
            if previous is not None:
                return previous
        recorded: str | None = encode_metadata(metadata)
        user_meta: dict[str, str] = {}
        if recorded is not None:
            user_meta["write-metadata"] = recorded
        if operation is not None:
            user_meta["operation-id"] = operation.id
            user_meta["fingerprint"] = operation.fingerprint
        payload: bytes = content.encode("utf-8")

        def swap() -> str:
            return self._objects.compare_and_swap(
                key,
                payload,
                "text/markdown",
                expected_etag=expected_version,
                metadata=user_meta,
            )

        try:
            etag: str = await anyio.to_thread.run_sync(swap)
        except PreconditionFailed as err:
            raise MemoryConflictError(
                f"memory path {path!r} changed before it could be written",
            ) from err
        if operation is not None:
            await self._put_receipt(operation, etag, existed=expected_version is not None)
        return MemoryMutation(
            version=etag,
            replayed=False,
            existed=expected_version is not None,
        )

    async def delete(
        self,
        path: str,
        *,
        expected_version: str | None,
        operation: MemoryOperation | None = None,
    ) -> MemoryMutation:
        """Delete ``path`` when ``expected_version`` is still current."""
        key: str = self._key(path)
        if operation is not None:
            previous: MemoryMutation | None = await self.get_operation(operation)
            if previous is not None:
                return previous

        def remove() -> None:
            self._objects.delete_if(key, expected_etag=expected_version)

        try:
            await anyio.to_thread.run_sync(remove)
        except PreconditionFailed as err:
            raise MemoryConflictError(
                f"memory path {path!r} changed before it could be deleted",
            ) from err
        mutation: MemoryMutation = MemoryMutation(
            version=None,
            replayed=False,
            existed=expected_version is not None,
        )
        if operation is not None:
            await self._put_receipt(operation, None, existed=mutation.existed)
        return mutation

    async def list_paths(self, prefix: str = "", *, limit: int) -> list[str]:
        """Return markdown paths under ``prefix``. Receipts stay hidden."""
        self._check_prefix(prefix)
        if limit <= 0:
            raise ValueError("limit must be positive")
        listed: list[StoredObject] = await anyio.to_thread.run_sync(
            self._objects.list,
            f"{self._root}{prefix}",
        )
        paths: list[str] = []
        item: StoredObject
        for item in listed:
            if not item.key.startswith(self._root):
                continue
            relative: str = item.key[len(self._root) :]
            if not _visible_note(relative):
                continue
            paths.append(relative)
            if len(paths) == limit:
                break
        return paths

    async def search(
        self,
        prefix: str,
        query: str,
        *,
        limit: int,
        max_files: int,
        max_chars: int,
        max_file_chars: int,
    ) -> MemorySearchResult:
        """Search markdown directly under ``prefix``."""
        self._check_prefix(prefix)
        if (
            not query.split()
            or limit <= 0
            or max_files <= 0
            or max_chars <= 0
            or max_file_chars <= 0
        ):
            return MemorySearchResult(matches=[], scanned=0, truncated=False)
        paths: list[str] = await self.list_paths(prefix, limit=max_files + 1)
        direct: list[str] = [path for path in paths if is_direct_child(prefix, path)]
        chosen: list[str] = direct[:max_files]
        files: list[tuple[str, str]] = []
        content_truncated: bool = False
        path: str
        for path in chosen:
            item: MemoryFile | None = await self.read(path, max_chars=max_file_chars)
            if item is None:
                continue
            if item.truncated:
                content_truncated = True
            files.append((path, item.content))
        result: MemorySearchResult = await lexical_search(
            files,
            query,
            limit=limit,
            max_files=max_files,
            max_chars=max_chars,
            score_prefix=prefix,
        )
        return MemorySearchResult(
            matches=result.matches,
            scanned=result.scanned,
            truncated=result.truncated or content_truncated or len(direct) > max_files,
        )

    def _key(self, path: str) -> str:
        try:
            validate_store_path(path)
        except ValueError as err:
            raise MemoryPathEscapeError(str(err)) from err
        return f"{self._root}{path}"

    def _check_prefix(self, prefix: str) -> None:
        try:
            validate_store_prefix(prefix)
        except ValueError as err:
            raise MemoryPathEscapeError(str(err)) from err

    def _receipt_key(self, operation_id: str) -> str:
        if _RECEIPT_ID.fullmatch(operation_id) is None or ".." in operation_id:
            raise MemoryOperationConflictError(f"operation id {operation_id!r} is invalid")
        return f"{self._root}.receipts/{operation_id}"

    async def _put_receipt(
        self,
        operation: MemoryOperation,
        version: str | None,
        existed: bool,
    ) -> None:
        receipt: _Receipt = _Receipt(
            fingerprint=operation.fingerprint,
            version=version,
            existed=existed,
        )
        payload: bytes = receipt.model_dump_json().encode()
        key: str = self._receipt_key(operation.id)
        await anyio.to_thread.run_sync(self._objects.put, key, payload, "application/json")


def _visible_note(relative: str) -> bool:
    hidden: bool = relative.startswith(".") or "/." in relative
    return relative.endswith(".md") and not hidden
