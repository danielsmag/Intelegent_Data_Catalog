"""Workspace page operator."""

from __future__ import annotations

from pydantic import Field, model_validator

from intelegent_data_catalog.assets.definitions.models import PageSourceRef
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.registry.identifiers import require_slug


class PageOperator(Operator):
    """Link a workspace file or notebook. The ``include_*`` fields are its build flags."""

    path: str = Field(min_length=1, max_length=2048)
    workspace: str = "shared"
    include_outputs: bool = False
    include_table_links: bool = True

    @model_validator(mode="after")
    def _workspace_slug(self) -> PageOperator:
        require_slug(self.workspace)
        return self

    def context_flags(self) -> dict[str, bool]:
        """File and notebook build flags."""
        return {
            "include_outputs": self.include_outputs,
            "include_table_links": self.include_table_links,
        }

    def to_source(self) -> PageSourceRef:
        """Return the stored source reference."""
        return PageSourceRef(workspace=self.workspace, path=self.path)
