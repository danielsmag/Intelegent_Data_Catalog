"""Upload examples/asset-definitions into the configured MinIO bucket."""

from pathlib import Path
from typing import Protocol, cast

import boto3
from botocore.exceptions import ClientError

from intelegent_data_catalog.core.settings import (
    AppSettings,
    ObjectStoreSettings,
    load_settings,
    resolve_project_root,
)
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.storage.object_store import definition_store_settings

_CONTENT_TYPE: str = "text/x-python"
_MISSING_BUCKET: frozenset[str] = frozenset({"404", "NoSuchBucket", "NotFound"})


class AssetDefinitionUploadError(CatalogError):
    """Raised when definition files cannot be written to object storage."""


class _S3Client(Protocol):
    """Subset of the boto3 client used to create a bucket and put files."""

    def head_bucket(self, *, Bucket: str) -> object:
        """Return when the bucket exists."""

    def create_bucket(
        self,
        *,
        Bucket: str,
        CreateBucketConfiguration: dict[str, str] | None = None,
    ) -> object:
        """Create a bucket. ``CreateBucketConfiguration`` is omitted for us-east-1."""
        ...

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object:
        """Store one object."""


class AssetDefinitionUploader:
    """Copies Python definition files to the asset-definition prefix."""

    def __init__(self, settings: AppSettings, source_dir: Path) -> None:
        self._settings: AppSettings = settings
        self._source_dir: Path = source_dir
        self._store: ObjectStoreSettings = definition_store_settings(
            settings.object_store,
            endpoint=settings.asset_definitions.endpoint,
            access_key=settings.asset_definitions.access_key,
            secret_key=settings.asset_definitions.secret_key,
            region=settings.asset_definitions.region,
            bucket=settings.asset_definitions.bucket,
        )

    def object_keys(self) -> list[tuple[str, Path]]:
        """Return storage keys and local paths for every definition file."""
        if not self._source_dir.is_dir():
            raise AssetDefinitionUploadError(f"definition directory not found: {self._source_dir}")
        prefix: str = self._settings.asset_definitions.prefix
        found: list[tuple[str, Path]] = []
        path: Path
        for path in sorted(self._source_dir.rglob("*.py")):
            if path.name == "__init__.py" or not path.is_file():
                continue
            relative: str = path.relative_to(self._source_dir).as_posix()
            found.append((f"{prefix}{relative}", path))
        return found

    def upload(self) -> list[str]:
        """Create the bucket when needed and put every definition file. Returns the keys."""
        if self._store.endpoint.strip() == "":
            raise AssetDefinitionUploadError("asset definition endpoint is empty")
        files: list[tuple[str, Path]] = self.object_keys()
        if len(files) == 0:
            raise AssetDefinitionUploadError(f"no Python files in {self._source_dir}")
        client: _S3Client = self._client()
        self._ensure_bucket(client)
        written: list[str] = []
        key: str
        path: Path
        for key, path in files:
            try:
                client.put_object(
                    Bucket=self._store.bucket,
                    Key=key,
                    Body=path.read_bytes(),
                    ContentType=_CONTENT_TYPE,
                )
            except ClientError as err:
                raise AssetDefinitionUploadError(f"failed to upload {key}") from err
            written.append(key)
        return written

    def _client(self) -> _S3Client:
        client: object = boto3.client(
            "s3",
            endpoint_url=self._store.endpoint,
            aws_access_key_id=self._store.access_key,
            aws_secret_access_key=self._store.secret_key.get_secret_value(),
            region_name=self._store.region,
        )
        return cast(_S3Client, client)

    def _ensure_bucket(self, client: _S3Client) -> None:
        bucket: str = self._store.bucket
        try:
            client.head_bucket(Bucket=bucket)
            return
        except ClientError as err:
            if _error_code(err) not in _MISSING_BUCKET:
                raise AssetDefinitionUploadError(f"cannot read bucket {bucket}") from err
        region: str = self._store.region
        try:
            if region in {"", "us-east-1"}:
                client.create_bucket(Bucket=bucket)
            else:
                client.create_bucket(
                    Bucket=bucket,
                    CreateBucketConfiguration={"LocationConstraint": region},
                )
        except ClientError as err:
            if _error_code(err) not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise AssetDefinitionUploadError(f"cannot create bucket {bucket}") from err


def _error_code(err: ClientError) -> str:
    response: object = getattr(err, "response", None)
    if not isinstance(response, dict):
        return ""
    error: object = response.get("Error")
    if not isinstance(error, dict):
        return ""
    code: object = error.get("Code")
    if isinstance(code, str):
        return code
    return ""


def main() -> None:
    """Upload the example definition files."""
    settings: AppSettings = load_settings()
    source: Path = resolve_project_root() / "examples" / "asset-definitions"
    uploader: AssetDefinitionUploader = AssetDefinitionUploader(settings, source)
    keys: list[str] = uploader.upload()
    bucket: str = definition_store_settings(
        settings.object_store,
        endpoint=settings.asset_definitions.endpoint,
        access_key=settings.asset_definitions.access_key,
        secret_key=settings.asset_definitions.secret_key,
        region=settings.asset_definitions.region,
        bucket=settings.asset_definitions.bucket,
    ).bucket
    key: str
    for key in keys:
        print(f"uploaded s3://{bucket}/{key}")


if __name__ == "__main__":
    main()
