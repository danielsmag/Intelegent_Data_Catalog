"""Shared fields for catalog-table operators."""

from pydantic import Field, model_validator

from intelegent_data_catalog.assets.definitions.models import TableSourceRef
from intelegent_data_catalog.assets.dsl.operators.base import Operator


class TableOperator(Operator):
    """Abstract link to a catalog table. Subclasses name one table kind."""

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
