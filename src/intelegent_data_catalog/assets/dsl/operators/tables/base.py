"""Shared fields for catalog-table operators."""

from __future__ import annotations

from pydantic import Field, model_validator

from intelegent_data_catalog.assets.definitions.models import TableSourceRef
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.assets.strategies.flags import TABLE_FLAGS


class TableContextOperator(Operator):
    """Build flags shared by one table and by a schema of tables."""

    include_graph: bool = TABLE_FLAGS["include_graph"]
    include_lineage: bool = TABLE_FLAGS["include_lineage"]
    include_data_profile: bool = TABLE_FLAGS["include_data_profile"]
    use_rules: bool = TABLE_FLAGS["use_rules"]
    use_skills: bool = TABLE_FLAGS["use_skills"]

    def context_flags(self) -> dict[str, bool]:
        """Table build flags."""
        flags: dict[str, bool] = {
            "include_graph": self.include_graph,
            "include_lineage": self.include_lineage,
            "include_data_profile": self.include_data_profile,
        }
        flags.update(self._explicit_pack_flags())
        return flags


class TableOperator(TableContextOperator):
    """Abstract link to a catalog table. Subclasses name one table kind.

    The ``include_*`` fields are the table context build flags the operator runs with.
    """

    source_key: str = Field(min_length=1, max_length=2048)
    data_source: str | None = None

    @model_validator(mode="after")
    def _data_source_not_blank(self) -> TableOperator:
        if self.data_source is not None and self.data_source.strip() == "":
            raise ValueError("data_source cannot be blank")
        return self

    def _source_ref(self) -> TableSourceRef:
        """Build the stored table reference from the shared fields."""
        data_source: str | None = None if self.data_source is None else self.data_source.strip()
        return TableSourceRef(source_key=self.source_key, data_source=data_source)
