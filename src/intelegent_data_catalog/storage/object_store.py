"""Workspace file bytes. An empty endpoint keeps an in-memory store."""

import hashlib
import threading
from datetime import datetime
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, SecretStr

from intelegent_data_catalog.core.settings import ObjectStoreSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError


class StoredObject(BaseModel):
    """One object key under a prefix. ``etag`` changes when the bytes change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    etag: str
    size_bytes: int
    last_modified: datetime | None = None


class StoredBody(BaseModel):
    """Object bytes plus the etag and user metadata from one read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    body: bytes
    etag: str
    metadata: dict[str, str]


class PreconditionFailed(Exception):
    """A conditional write or delete did not match the current etag."""

    def __init__(self, key: str) -> None:
        self.key: str = key
        super().__init__(key)


class ObjectStore(Protocol):
    """Put, get, delete, and list opaque objects by key."""

    def put(self, key: str, body: bytes, content_type: str) -> None:
        """Store ``body`` at ``key``."""

    def get(self, key: str) -> bytes:
        """Return the bytes stored at ``key``."""
        ...

    def read_object(self, key: str) -> StoredBody:
        """Return bytes, etag, and user metadata. Missing keys raise."""
        ...

    def compare_and_swap(
        self,
        key: str,
        body: bytes,
        content_type: str,
        *,
        expected_etag: str | None,
        metadata: dict[str, str],
    ) -> str:
        """Replace ``key`` when ``expected_etag`` is current.

        ``None`` creates the key only when it is absent. Returns the new etag.
        """
        ...

    def delete_if(self, key: str, *, expected_etag: str | None) -> None:
        """Delete ``key`` when its etag is ``expected_etag``.

        ``None`` succeeds only when the key is already absent.
        """
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``. Missing keys are ignored."""

    def list(self, prefix: str) -> list[StoredObject]:
        """Return every object whose key starts with ``prefix``, including subfolders."""
        ...


class _Record:
    """One process-local object."""

    def __init__(self, body: bytes, content_type: str, metadata: dict[str, str]) -> None:
        self.body: bytes = body
        self.content_type: str = content_type
        self.metadata: dict[str, str] = metadata


class MemoryObjectStore:
    """Process-local object store used by tests and host dev."""

    def __init__(self) -> None:
        self._objects: dict[str, _Record] = {}
        self._lock: threading.Lock = threading.Lock()

    def put(self, key: str, body: bytes, content_type: str) -> None:
        """Store ``body`` at ``key``."""
        with self._lock:
            self._objects[key] = _Record(body, content_type, {})

    def get(self, key: str) -> bytes:
        """Return the bytes stored at ``key``."""
        return self.read_object(key).body

    def read_object(self, key: str) -> StoredBody:
        """Return bytes, etag, and user metadata."""
        with self._lock:
            found: _Record | None = self._objects.get(key)
            if found is None:
                raise EntityNotFoundError("object", key)
            return StoredBody(
                body=found.body,
                etag=_etag(found.body),
                metadata=dict(found.metadata),
            )

    def compare_and_swap(
        self,
        key: str,
        body: bytes,
        content_type: str,
        *,
        expected_etag: str | None,
        metadata: dict[str, str],
    ) -> str:
        """Replace ``key`` when ``expected_etag`` is still current."""
        with self._lock:
            found: _Record | None = self._objects.get(key)
            current: str | None = None if found is None else _etag(found.body)
            if current != expected_etag:
                raise PreconditionFailed(key)
            self._objects[key] = _Record(body, content_type, dict(metadata))
            return _etag(body)

    def delete_if(self, key: str, *, expected_etag: str | None) -> None:
        """Delete ``key`` when its etag is ``expected_etag``."""
        with self._lock:
            found: _Record | None = self._objects.get(key)
            current: str | None = None if found is None else _etag(found.body)
            if current != expected_etag:
                raise PreconditionFailed(key)
            if found is not None:
                del self._objects[key]

    def delete(self, key: str) -> None:
        """Remove ``key``. Missing keys are ignored."""
        with self._lock:
            self._objects.pop(key, None)

    def list(self, prefix: str) -> list[StoredObject]:
        """Return every object whose key starts with ``prefix``."""
        with self._lock:
            found: list[StoredObject] = []
            key: str
            record: _Record
            for key, record in self._objects.items():
                if not key.startswith(prefix):
                    continue
                found.append(
                    StoredObject(
                        key=key,
                        etag=_etag(record.body),
                        size_bytes=len(record.body),
                        last_modified=None,
                    ),
                )
            found.sort(key=_stored_key)
            return found


class _S3Client(Protocol):
    """Subset of the boto3 S3 client used for workspace files."""

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        Metadata: dict[str, str] | None = None,
        IfMatch: str | None = None,
        IfNoneMatch: str | None = None,
    ) -> dict[str, object]:
        """Store one object. ``IfMatch`` and ``IfNoneMatch`` are conditional."""
        ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        """Load one object."""
        ...

    def delete_object(self, *, Bucket: str, Key: str, IfMatch: str | None = None) -> object:
        """Delete one object when ``IfMatch`` is omitted or equals the current etag."""

    def list_objects_v2(
        self,
        *,
        Bucket: str,
        Prefix: str,
        ContinuationToken: str | None = None,
    ) -> dict[str, object]:
        """List objects under ``Prefix``. ``ContinuationToken`` requests the next page."""
        ...


class S3ObjectStore:
    """S3-compatible object store. boto3 is imported when the store is built."""

    def __init__(self, settings: ObjectStoreSettings) -> None:
        import boto3

        self._bucket: str = settings.bucket
        client: object = boto3.client(
            "s3",
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key.get_secret_value(),
            region_name=settings.region,
        )
        self._client: _S3Client = cast(_S3Client, client)

    def put(self, key: str, body: bytes, content_type: str) -> None:
        """Store ``body`` at ``key``."""
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )

    def get(self, key: str) -> bytes:
        """Return the bytes stored at ``key``."""
        return self.read_object(key).body

    def read_object(self, key: str) -> StoredBody:
        """Return bytes, etag, and user metadata."""
        try:
            response: dict[str, object] = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as err:
            raise EntityNotFoundError("object", key) from err
        return StoredBody(
            body=_response_body(key, response),
            etag=_response_etag(response),
            metadata=_response_metadata(response),
        )

    def compare_and_swap(
        self,
        key: str,
        body: bytes,
        content_type: str,
        *,
        expected_etag: str | None,
        metadata: dict[str, str],
    ) -> str:
        """Replace ``key`` when ``expected_etag`` is still current."""
        try:
            if expected_etag is None:
                response: dict[str, object] = self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    Metadata=metadata,
                    IfNoneMatch="*",
                )
            else:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    Metadata=metadata,
                    IfMatch=_quoted_etag(expected_etag),
                )
        except Exception as err:
            if _precondition(err):
                raise PreconditionFailed(key) from err
            raise
        return _response_etag(response)

    def delete_if(self, key: str, *, expected_etag: str | None) -> None:
        """Delete ``key`` when its etag is ``expected_etag``."""
        if expected_etag is None:
            try:
                self.read_object(key)
            except EntityNotFoundError:
                return
            raise PreconditionFailed(key)
        try:
            self._client.delete_object(
                Bucket=self._bucket,
                Key=key,
                IfMatch=_quoted_etag(expected_etag),
            )
        except Exception as err:
            if _precondition(err) or _missing(err):
                raise PreconditionFailed(key) from err
            raise

    def delete(self, key: str) -> None:
        """Remove ``key``. Missing keys are ignored."""
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def list(self, prefix: str) -> list[StoredObject]:
        """Return every object under ``prefix``. Subfolders are included."""
        found: list[StoredObject] = []
        token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            page: dict[str, object] = self._page(prefix, token)
            found.extend(_page_objects(page))
            if page.get("IsTruncated") is not True:
                break
            next_token: object = page.get("NextContinuationToken")
            if not isinstance(next_token, str) or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
            token = next_token
        found.sort(key=_stored_key)
        return found

    def _page(self, prefix: str, token: str | None) -> dict[str, object]:
        if token is None:
            return self._client.list_objects_v2(Bucket=self._bucket, Prefix=prefix)
        return self._client.list_objects_v2(
            Bucket=self._bucket,
            Prefix=prefix,
            ContinuationToken=token,
        )


def _etag(body: bytes) -> str:
    return hashlib.md5(body, usedforsecurity=False).hexdigest()


def _quoted_etag(etag: str) -> str:
    if etag.startswith('"'):
        return etag
    return f'"{etag}"'


def _response_body(key: str, response: dict[str, object]) -> bytes:
    raw: object = response.get("Body")
    read: object = getattr(raw, "read", None)
    if not callable(read):
        raise EntityNotFoundError("object", key)
    payload: object = read()
    if not isinstance(payload, bytes):
        raise EntityNotFoundError("object", key)
    return payload


def _response_etag(response: dict[str, object]) -> str:
    raw: object = response.get("ETag")
    if isinstance(raw, str):
        cleaned: str = raw.strip('"')
        if cleaned != "":
            return cleaned
    body: object = response.get("Body")
    if isinstance(body, bytes):
        return _etag(body)
    return ""


def _response_metadata(response: dict[str, object]) -> dict[str, str]:
    raw: object = response.get("Metadata")
    if not isinstance(raw, dict):
        return {}
    found: dict[str, str] = {}
    key: object
    value: object
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            found[key.lower()] = value
    return found


def _error_parts(err: BaseException) -> tuple[str, int | None]:
    response: object = getattr(err, "response", None)
    if not isinstance(response, dict):
        return "", None
    error: object = response.get("Error")
    code: str = ""
    if isinstance(error, dict):
        raw_code: object = error.get("Code")
        if isinstance(raw_code, str):
            code = raw_code
    meta: object = response.get("ResponseMetadata")
    status: int | None = None
    if isinstance(meta, dict):
        raw_status: object = meta.get("HTTPStatusCode")
        if isinstance(raw_status, int):
            status = raw_status
    return code, status


def _precondition(err: BaseException) -> bool:
    code: str
    status: int | None
    code, status = _error_parts(err)
    if code in {"PreconditionFailed", "ConditionalRequestConflict"}:
        return True
    return status in {409, 412}


def _missing(err: BaseException) -> bool:
    code: str
    status: int | None
    code, status = _error_parts(err)
    if code in {"NoSuchKey", "NotFound", "404"}:
        return True
    return status == 404


def _stored_key(item: StoredObject) -> str:
    return item.key


def _page_objects(page: dict[str, object]) -> list[StoredObject]:
    contents: object = page.get("Contents")
    if not isinstance(contents, list):
        return []
    found: list[StoredObject] = []
    raw: object
    for raw in contents:
        listed: StoredObject | None = _one_object(raw)
        if listed is not None:
            found.append(listed)
    return found


def _one_object(raw: object) -> StoredObject | None:
    if not isinstance(raw, dict):
        return None
    key: object = raw.get("Key")
    if not isinstance(key, str) or key.endswith("/"):
        return None
    etag_raw: object = raw.get("ETag")
    etag: str = etag_raw.strip('"') if isinstance(etag_raw, str) else ""
    size_raw: object = raw.get("Size")
    size_bytes: int = size_raw if isinstance(size_raw, int) and size_raw >= 0 else 0
    modified: object = raw.get("LastModified")
    last_modified: datetime | None = modified if isinstance(modified, datetime) else None
    return StoredObject(
        key=key,
        etag=etag,
        size_bytes=size_bytes,
        last_modified=last_modified,
    )


def definition_store_settings(
    base: ObjectStoreSettings,
    *,
    endpoint: str,
    access_key: str,
    secret_key: SecretStr,
    region: str,
    bucket: str,
) -> ObjectStoreSettings:
    """Definition store. Blank fields fall back to the workspace object store."""
    chosen_secret: SecretStr = secret_key
    if secret_key.get_secret_value().strip() == "":
        chosen_secret = base.secret_key
    return base.model_copy(
        update={
            "endpoint": endpoint.strip() or base.endpoint,
            "access_key": access_key.strip() or base.access_key,
            "secret_key": chosen_secret,
            "region": region.strip() or base.region,
            "bucket": bucket.strip() or base.bucket,
        },
    )


def build_object_store(settings: ObjectStoreSettings) -> ObjectStore:
    """Use memory until an endpoint is configured."""
    if settings.endpoint.strip() == "":
        return MemoryObjectStore()
    return S3ObjectStore(settings)
