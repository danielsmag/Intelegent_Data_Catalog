"""Workspace page operator."""

from __future__ import annotations

from pydantic import Field, model_validator

from intelegent_data_catalog.assets.definitions.models import PageSourceRef
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.assets.strategies.flags import FILE_FLAGS
from intelegent_data_catalog.registry.identifiers import require_slug


class PageOperator(Operator):
    """Link a workspace file or notebook. The ``include_*`` fields are its build flags."""

    path: str = Field(min_length=1, max_length=2048)
    workspace: str = "shared"
    include_outputs: bool = FILE_FLAGS["include_outputs"]
    include_table_links: bool = FILE_FLAGS["include_table_links"]
    use_rules: bool = FILE_FLAGS["use_rules"]
    use_skills: bool = FILE_FLAGS["use_skills"]

    @model_validator(mode="after")
    def _workspace_slug(self) -> PageOperator:
        require_slug(self.workspace)
        return self

    def context_flags(self) -> dict[str, bool]:
        """File and notebook build flags."""
        flags: dict[str, bool] = {
            "include_outputs": self.include_outputs,
            "include_table_links": self.include_table_links,
        }
        flags.update(self._explicit_pack_flags())
        return flags

    def to_source(self) -> PageSourceRef:
        """Return the stored source reference."""
        return PageSourceRef(workspace=self.workspace, path=self.path)
