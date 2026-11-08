"""Sources name what an asset reads. Operators are the steps that run on it."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from intelegent_data_catalog.assets.definitions.models import (
    AirflowInstanceSourceRef,
    GitHubRepoSourceRef,
    IcebergCatalogSourceRef,
    IcebergSchemaSourceRef,
    IcebergTableSourceRef,
    SourceRef,
)
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.registry.identifiers import require_slug

_GITHUB: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class Source(BaseModel):
    """The object an asset stands for. Construction does not read the remote system."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_ref(self) -> SourceRef:
        """Return the stored source reference."""
        raise NotImplementedError


class AirflowInstance(Source):
    """One registered Airflow instance, named by slug."""

    instance: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def _slug(self) -> AirflowInstance:
        try:
            require_slug(self.instance)
        except ValueError as err:
            raise AssetDefinitionError(f"instance: {err}") from err
        return self

    def to_ref(self) -> AirflowInstanceSourceRef:
        """Return the stored instance reference."""
        return AirflowInstanceSourceRef(instance=self.instance)


class IcebergTable(Source):
    """One Iceberg table in a Polaris catalog."""

    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    table: str = Field(min_length=1, max_length=512)

    def to_ref(self) -> IcebergTableSourceRef:
        """Return the stored table reference."""
        return IcebergTableSourceRef(
            catalog=self.catalog.strip(),
            namespace=self.namespace.strip(),
            table=self.table.strip(),
        )


class IcebergSchema(Source):
    """Every table in one Iceberg namespace."""

    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    include: list[str] | None = None
    exclude: list[str] = Field(default_factory=list)

    def to_ref(self) -> IcebergSchemaSourceRef:
        """Return the stored schema reference."""
        return IcebergSchemaSourceRef(
            catalog=self.catalog.strip(),
            namespace=self.namespace.strip(),
            include=None if self.include is None else list(self.include),
            exclude=list(self.exclude),
        )


class IcebergCatalog(Source):
    """Every namespace in one Iceberg catalog."""

    catalog: str = Field(min_length=1, max_length=512)
    include: list[str] | None = None
    exclude: list[str] = Field(default_factory=list)

    def to_ref(self) -> IcebergCatalogSourceRef:
        """Return the stored catalog reference."""
        return IcebergCatalogSourceRef(
            catalog=self.catalog.strip(),
            include=None if self.include is None else list(self.include),
            exclude=list(self.exclude),
        )


class GitHubRepo(Source):
    """One GitHub repository. ``branch`` is the ref a new commit reruns."""

    owner: str = Field(min_length=1, max_length=100)
    repo: str = Field(min_length=1, max_length=100)
    branch: str = Field(default="master", min_length=1, max_length=255)

    @model_validator(mode="after")
    def _names(self) -> GitHubRepo:
        if _GITHUB.fullmatch(self.owner) is None or _GITHUB.fullmatch(self.repo) is None:
            raise AssetDefinitionError(
                "GitHub owner and repo must use letters, digits, '_', '.' or '-'",
            )
        if self.branch.strip() == "" or ".." in self.branch.split("/") or "\x00" in self.branch:
            raise AssetDefinitionError("GitHub branch is not a valid ref")
        return self

    def to_ref(self) -> GitHubRepoSourceRef:
        """Return the stored repository reference."""
        return GitHubRepoSourceRef(owner=self.owner, repo=self.repo, branch=self.branch.strip())
