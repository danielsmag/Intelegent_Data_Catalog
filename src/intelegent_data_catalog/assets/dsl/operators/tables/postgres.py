"""Postgres table operator."""

from intelegent_data_catalog.assets.definitions.models import TableSourceRef
from intelegent_data_catalog.assets.dsl.operators.tables.base import TableOperator


class PostgresTableOperator(TableOperator):
    """Link a Postgres table by its source key."""

    def to_source(self) -> TableSourceRef:
        """Return the stored source reference."""
        return self._source_ref()
