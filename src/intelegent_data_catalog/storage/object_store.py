"""Workspace file bytes. An empty endpoint keeps an in-memory store."""

import hashlib
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


class ObjectStore(Protocol):
    """Put, get, delete, and list opaque objects by key."""

    def put(self, key: str, body: bytes, content_type: str) -> None:
        """Store ``body`` at ``key``."""

    def get(self, key: str) -> bytes:
        """Return the bytes stored at ``key``."""
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``. Missing keys are ignored."""

    def list(self, prefix: str) -> list[StoredObject]:
        """Return every object whose key starts with ``prefix``, including subfolders."""
        ...


class MemoryObjectStore:
    """Process-local object store used by tests and host dev."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, body: bytes, content_type: str) -> None:
        """Store ``body`` at ``key``."""
        self._objects[key] = (body, content_type)

    def get(self, key: str) -> bytes:
        """Return the bytes stored at ``key``."""
        found: tuple[bytes, str] | None = self._objects.get(key)
        if found is None:
            raise EntityNotFoundError("object", key)
        return found[0]

    def delete(self, key: str) -> None:
        """Remove ``key``. Missing keys are ignored."""
        self._objects.pop(key, None)

    def list(self, prefix: str) -> list[StoredObject]:
        """Return every object whose key starts with ``prefix``."""
        found: list[StoredObject] = []
        key: str
        body: bytes
        for key, (body, _content_type) in self._objects.items():
            if not key.startswith(prefix):
                continue
            found.append(
                StoredObject(
                    key=key,
                    etag=hashlib.md5(body, usedforsecurity=False).hexdigest(),
                    size_bytes=len(body),
                    last_modified=None,
                ),
            )
        found.sort(key=_stored_key)
        return found


class _S3Client(Protocol):
    """Subset of the boto3 S3 client used for workspace files."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object:
        """Store one object."""

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        """Load one object."""
        ...

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        """Delete one object."""

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
        try:
            response: dict[str, object] = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as err:
            raise EntityNotFoundError("object", key) from err
        raw: object = response.get("Body")
        read = getattr(raw, "read", None)
        if not callable(read):
            raise EntityNotFoundError("object", key)
        payload: object = read()
        if not isinstance(payload, bytes):
            raise EntityNotFoundError("object", key)
        return payload

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
