"""Data profiler contract. Implementations talk to physical tables. Agents do not."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.exceptions import ProfileUnavailableError
from intelegent_data_catalog.knowledge.models import (
    ColumnComparisonProfile,
    ColumnDataProfile,
    DataProfileOptions,
    TableDataProfile,
)


class DataProfiler(Protocol):
    """Read-only statistics for catalog tables.

    An implementation must enforce the timeout, stay read-only, and cap rows.
    Sample values are allowed only when the access mode is ``SAMPLED`` and must
    not exceed ``max_sample_values``. Unrestricted ``SELECT`` is not part of
    this contract.
    """

    def profile_table(
        self,
        table_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> TableDataProfile:
        """Return table and column statistics. Do not return the full table."""
        ...

    def profile_column(
        self,
        column_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> ColumnDataProfile:
        """Return statistics for one column."""
        ...

    def compare_columns(
        self,
        source_column_id: UUID,
        target_column_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> ColumnComparisonProfile:
        """Return overlap ratios for two columns. Do not return the values."""
        ...


class UnconfiguredDataProfiler:
    """Profiler stand-in. Physical statistics are a later milestone."""

    def profile_table(
        self,
        table_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> TableDataProfile:
        """Refuse a physical table profile."""
        del table_id, options
        raise ProfileUnavailableError("data profiler is not configured")

    def profile_column(
        self,
        column_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> ColumnDataProfile:
        """Refuse a physical column profile."""
        del column_id, options
        raise ProfileUnavailableError("data profiler is not configured")

    def compare_columns(
        self,
        source_column_id: UUID,
        target_column_id: UUID,
        options: DataProfileOptions | None = None,
    ) -> ColumnComparisonProfile:
        """Refuse a physical column comparison."""
        del source_column_id, target_column_id, options
        raise ProfileUnavailableError("data profiler is not configured")
