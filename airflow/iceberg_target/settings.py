"""Catalog connection settings for the Polaris warehouse."""

from __future__ import annotations

import os
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from iceberg_target.errors import IcebergConfigError


class IcebergSettings(BaseModel):
    """REST catalog and warehouse endpoint used to open existing tables."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    polaris_uri: str
    polaris_token_uri: str
    client_id: str
    client_secret: str
    catalog_name: str
    realm: str
    s3_endpoint: str
    s3_region: str
    namespace: str

    @classmethod
    def from_env(cls) -> IcebergSettings:
        """Read the Polaris principal and warehouse endpoint from the environment."""
        values: dict[str, str] = {
            "polaris_uri": _required("POLARIS_URI"),
            "polaris_token_uri": _required("POLARIS_TOKEN_URI"),
            "client_id": _required("POLARIS_CLIENT_ID"),
            "client_secret": _required("POLARIS_CLIENT_SECRET"),
            "catalog_name": _required("POLARIS_CATALOG_NAME"),
            "realm": _required("POLARIS_REALM"),
            "s3_endpoint": _required("S3_ENDPOINT"),
            "s3_region": os.environ.get("AWS_REGION", "us-west-2"),
            "namespace": os.environ.get("ICEBERG_NAMESPACE", "sales"),
        }
        return cls.model_validate(values)


def _required(name: str) -> str:
    value: str | None = os.environ.get(name)
    if value is None or value == "":
        raise IcebergConfigError(name)
    return value
