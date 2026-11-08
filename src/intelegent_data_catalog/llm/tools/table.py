"""Catalog tools. Each one reads through the table agent passed as deps."""

from collections.abc import Callable
from typing import Annotated, Protocol, cast
from uuid import UUID

from langchain_core.tools import tool
from pydantic import Field

from intelegent_data_catalog.exceptions import GraphUnavailableError, ValidationAppError
from intelegent_data_catalog.knowledge.models import (
    ColumnComparisonProfile,
    ColumnDataProfile,
    DataAccessMode,
    GraphNeighbor,
    GraphPublishResult,
    LineageContext,
    ProfileOverrides,
    TableCatalogContext,
    TableDataProfile,
)
from intelegent_data_catalog.llm.errors import ToolRetry
from intelegent_data_catalog.llm.tool_deps import current_tool_deps

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

    def publish_graph(self) -> GraphPublishResult:
        """Replace the Neo4j projection from PostgreSQL and Marquez."""
        ...


def _source() -> TableToolSource:
    return cast(TableToolSource, current_tool_deps())


@tool
def get_metadata() -> TableCatalogContext:
    """Load columns, keys, relationships, scope, and tags for this table."""
    return _source().get_metadata()


@tool
def get_data_profile(
    access_mode: DataAccessMode | None = None,
    max_sample_values: SampleLimit | None = None,
) -> TableDataProfile:
    """Load row counts and column statistics for this table.

    Args:
        access_mode: How much physical data to read.
        max_sample_values: Sample values allowed when access_mode is SAMPLED.
    """
    return _read(lambda: _source().get_data_profile(_overrides(access_mode, max_sample_values)))


@tool
def get_column_profile(
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
        lambda: _source().get_column_profile(
            column_id,
            _overrides(access_mode, max_sample_values),
        )
    )


@tool
def compare_columns(
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
        lambda: _source().compare_columns(
            source_column_id,
            target_column_id,
            _overrides(access_mode, max_sample_values),
        )
    )


@tool
def get_neighbors() -> list[GraphNeighbor]:
    """Load catalog tables connected to this table in the graph."""
    return _source().get_neighbors()


@tool
def get_lineage() -> LineageContext:
    """Load upstream datasets, downstream datasets, and jobs for this table."""
    return _source().get_lineage()


@tool
def publish_graph() -> GraphPublishResult:
    """Replace the Neo4j projection from PostgreSQL and Marquez.

    This rewrites the whole projection, not only this table.
    """
    try:
        return _source().publish_graph()
    except GraphUnavailableError as err:
        raise ToolRetry(str(err)) from err


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
        raise ToolRetry(str(err)) from err
