"""Workspace file bytes. An empty endpoint keeps an in-memory store."""

from typing import Protocol, cast

from intelegent_data_catalog.core.settings import ObjectStoreSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError


class ObjectStore(Protocol):
    """Put, get, and delete opaque objects by key."""

    def put(self, key: str, body: bytes, content_type: str) -> None:
        """Store ``body`` at ``key``."""

    def get(self, key: str) -> bytes:
        """Return the bytes stored at ``key``."""
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``. Missing keys are ignored."""


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


class _S3Client(Protocol):
    """Subset of the boto3 S3 client used for workspace files."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object:
        """Store one object."""

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        """Load one object."""
        ...

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        """Delete one object."""


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


def build_object_store(settings: ObjectStoreSettings) -> ObjectStore:
    """Use memory until an endpoint is configured."""
    if settings.endpoint.strip() == "":
        return MemoryObjectStore()
    return S3ObjectStore(settings)
