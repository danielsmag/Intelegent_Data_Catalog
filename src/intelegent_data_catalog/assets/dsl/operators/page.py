"""Workspace page operator."""

from pydantic import Field, model_validator

from intelegent_data_catalog.assets.definitions.models import PageSourceRef
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.registry.identifiers import require_slug


class PageOperator(Operator):
    """Link a workspace file or notebook."""

    path: str = Field(min_length=1, max_length=2048)
    workspace: str = "shared"

    @model_validator(mode="after")
    def _workspace_slug(self) -> PageOperator:
        require_slug(self.workspace)
        return self

    def to_source(self) -> PageSourceRef:
        """Return the stored source reference."""
        return PageSourceRef(workspace=self.workspace, path=self.path)
