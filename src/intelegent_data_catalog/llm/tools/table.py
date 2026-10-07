"""Catalog tools. Each one reads through the table agent passed as ``deps``."""

from collections.abc import Callable
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import Field
from pydantic_ai import ModelRetry, RunContext

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.models import (
    ColumnComparisonProfile,
    ColumnDataProfile,
    DataAccessMode,
    GraphNeighbor,
    LineageContext,
    ProfileOverrides,
    TableCatalogContext,
    TableDataProfile,
)

SampleLimit = Annotated[int, Field(ge=0, le=20)]


class TableToolSource(Protocol):
    """Reads one table. ``TableAgent`` already implements this."""

    def get_metadata(self) -> TableCatalogContext:
        """Load the table, columns, keys, relationships, scope, space, and tags."""
        ...

    def get_data_profile(self, options: ProfileOverrides | None = None) -> TableDataProfile:
        """Load table statistics within the access mode."""
        ...

    def get_column_profile(
        self,
        column_id: UUID,
        options: ProfileOverrides | None = None,
    ) -> ColumnDataProfile:
        """Load statistics for one column on this table."""
        ...

    def compare_columns(
        self,
        source_column_id: UUID,
        target_column_id: UUID,
        options: ProfileOverrides | None = None,
    ) -> ColumnComparisonProfile:
        """Load overlap ratios. The source column must belong to this table."""
        ...

    def get_neighbors(self) -> list[GraphNeighbor]:
        """Load neighboring tables."""
        ...

    def get_lineage(self) -> LineageContext:
        """Load upstream, downstream, and jobs."""
        ...


def get_metadata(ctx: RunContext[TableToolSource]) -> TableCatalogContext:
    """Load columns, keys, relationships, scope, and tags for this table."""
    return ctx.deps.get_metadata()


def get_data_profile(
    ctx: RunContext[TableToolSource],
    access_mode: DataAccessMode | None = None,
    max_sample_values: SampleLimit | None = None,
) -> TableDataProfile:
    """Load row counts and column statistics for this table.

    Args:
        access_mode: How much physical data to read.
        max_sample_values: Sample values allowed when access_mode is SAMPLED.
    """
    return _read(lambda: ctx.deps.get_data_profile(_overrides(access_mode, max_sample_values)))


def get_column_profile(
    ctx: RunContext[TableToolSource],
    column_id: UUID,
    access_mode: DataAccessMode | None = None,
    max_sample_values: SampleLimit | None = None,
) -> ColumnDataProfile:
    """Load statistics for one column on this table.

    Args:
        column_id: Catalog column id.
        access_mode: How much physical data to read.
        max_sample_values: Sample values allowed when access_mode is SAMPLED.
    """
    return _read(
        lambda: ctx.deps.get_column_profile(
            column_id,
            _overrides(access_mode, max_sample_values),
        )
    )


def compare_columns(
    ctx: RunContext[TableToolSource],
    source_column_id: UUID,
    target_column_id: UUID,
    access_mode: DataAccessMode | None = None,
    max_sample_values: SampleLimit | None = None,
) -> ColumnComparisonProfile:
    """Load overlap ratios. source_column_id must belong to this table.

    Args:
        source_column_id: Column id on this table.
        target_column_id: Column id to compare against.
        access_mode: How much physical data to read.
        max_sample_values: Sample values allowed when access_mode is SAMPLED.
    """
    return _read(
        lambda: ctx.deps.compare_columns(
            source_column_id,
            target_column_id,
            _overrides(access_mode, max_sample_values),
        )
    )


def get_neighbors(ctx: RunContext[TableToolSource]) -> list[GraphNeighbor]:
    """Load catalog tables connected to this table in the graph."""
    return ctx.deps.get_neighbors()


def get_lineage(ctx: RunContext[TableToolSource]) -> LineageContext:
    """Load upstream datasets, downstream datasets, and jobs for this table."""
    return ctx.deps.get_lineage()


def _overrides(
    access_mode: DataAccessMode | None,
    max_sample_values: int | None,
) -> ProfileOverrides | None:
    if access_mode is None and max_sample_values is None:
        return None
    return ProfileOverrides(access_mode=access_mode, max_sample_values=max_sample_values)


def _read[T](load: Callable[[], T]) -> T:
    """Return the catalog value. A domain rejection is sent back to the model."""
    try:
        return load()
    except ValidationAppError as err:
        raise ModelRetry(str(err)) from err
