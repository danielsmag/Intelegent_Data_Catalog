"""Enforce profile bounds before statistics leave the table agent."""

from uuid import UUID

from pydantic import JsonValue

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.models import (
    ColumnComparisonProfile,
    ColumnContextItem,
    ColumnDataProfile,
    ColumnMetadata,
    DataAccessMode,
    DataProfileOptions,
    KnowledgeDefaults,
    ProfileOverrides,
    TableDataProfile,
)


def profiles_physical_data(mode: DataAccessMode) -> bool:
    """Return whether this mode may read physical table statistics."""
    match mode:
        case DataAccessMode.NONE | DataAccessMode.METADATA_ONLY:
            return False
        case DataAccessMode.PROFILE_ONLY | DataAccessMode.SAMPLED:
            return True
        case _:
            raise ValidationAppError("unknown data access mode", field="access_mode")


def resolve_profile_options(
    requested: ProfileOverrides | None,
    defaults: KnowledgeDefaults,
) -> DataProfileOptions:
    """Apply factory limits. Sample values stay at zero unless mode is ``SAMPLED``."""
    mode: DataAccessMode = defaults.data_access_mode
    timeout_seconds: float = defaults.profile_timeout_seconds
    if requested is not None and requested.access_mode is not None:
        mode = requested.access_mode
    if (
        requested is not None
        and requested.timeout_seconds is not None
        and requested.timeout_seconds < timeout_seconds
    ):
        timeout_seconds = requested.timeout_seconds
    sample_cap: int = 0
    if mode == DataAccessMode.SAMPLED:
        sample_cap = defaults.max_sample_values
        if (
            requested is not None
            and requested.max_sample_values is not None
            and requested.max_sample_values < sample_cap
        ):
            sample_cap = requested.max_sample_values
    return DataProfileOptions(
        access_mode=mode,
        max_sample_values=sample_cap,
        timeout_seconds=timeout_seconds,
    )


def accept_table_profile(
    profile: TableDataProfile,
    table_id: UUID,
    allowed_column_ids: set[UUID],
    options: DataProfileOptions,
) -> TableDataProfile:
    """Reject a profile for another table or an unknown column, then drop extra samples."""
    if profile.table_id != table_id:
        raise ValidationAppError("profiler returned a different table", field="table_id")
    column: ColumnDataProfile
    for column in profile.columns:
        if column.column_id not in allowed_column_ids:
            raise ValidationAppError(
                "profiler returned a column that is not on the table",
                field="column_id",
            )
    redacted: list[ColumnDataProfile] = [
        redact_column_profile(column, options) for column in profile.columns
    ]
    return profile.model_copy(update={"access_mode": options.access_mode, "columns": redacted})


def accept_column_profile(
    profile: ColumnDataProfile,
    column_id: UUID,
    options: DataProfileOptions,
) -> ColumnDataProfile:
    """Reject a profile for a different column, then drop extra samples."""
    if profile.column_id != column_id:
        raise ValidationAppError("profiler returned a different column", field="column_id")
    return redact_column_profile(profile, options)


def accept_comparison(
    profile: ColumnComparisonProfile,
    source_column_id: UUID,
    target_column_id: UUID,
) -> ColumnComparisonProfile:
    """Reject a comparison whose column ids differ from the request."""
    if profile.source_column_id != source_column_id or profile.target_column_id != target_column_id:
        raise ValidationAppError("profiler returned a different column pair", field="column_id")
    return profile


def accept_column_contexts(
    columns: list[ColumnMetadata],
    proposed: list[ColumnContextItem],
) -> list[ColumnContextItem]:
    """Reject a column id outside the batch, a duplicate, or a column left out."""
    allowed: set[UUID] = {column.id for column in columns}
    seen: set[UUID] = set()
    item: ColumnContextItem
    for item in proposed:
        if item.column_id not in allowed:
            raise ValidationAppError("column is not on this table", field="column_id")
        if item.column_id in seen:
            raise ValidationAppError("column context is duplicated", field="column_id")
        seen.add(item.column_id)
    if allowed - seen:
        raise ValidationAppError("context is missing for a column", field="column_id")
    return proposed


def accept_named_column_contexts(
    columns: list[ColumnMetadata],
    proposed: list[tuple[str, str]],
) -> list[ColumnContextItem]:
    """Map column names to ids. Reject an unknown name, a duplicate, or a column left out."""
    by_name: dict[str, UUID] = {column.name: column.id for column in columns}
    seen: set[str] = set()
    items: list[ColumnContextItem] = []
    pair: tuple[str, str]
    for pair in proposed:
        name: str = pair[0]
        text: str = pair[1]
        if name not in by_name:
            expected: str = ", ".join(column.name for column in columns)
            raise ValidationAppError(
                f"column {name} is not in this batch. Use: {expected}",
                field="name",
            )
        if name in seen:
            raise ValidationAppError("column context is duplicated", field="name")
        seen.add(name)
        items.append(ColumnContextItem(column_id=by_name[name], context=text))
    missing: set[str] = set(by_name) - seen
    if missing:
        left: str = ", ".join(sorted(missing))
        raise ValidationAppError(f"context is missing for: {left}", field="name")
    return items


def redact_column_profile(
    profile: ColumnDataProfile,
    options: DataProfileOptions,
) -> ColumnDataProfile:
    """Keep samples only for ``SAMPLED``, and never more than the configured cap."""
    samples: list[JsonValue] | None = _bounded_samples(profile.sample_values, options)
    return profile.model_copy(update={"sample_values": samples})


def _bounded_samples(
    samples: list[JsonValue] | None,
    options: DataProfileOptions,
) -> list[JsonValue] | None:
    match options.access_mode:
        case DataAccessMode.SAMPLED:
            if samples is None:
                return None
            return list(samples[: options.max_sample_values])
        case DataAccessMode.NONE | DataAccessMode.METADATA_ONLY | DataAccessMode.PROFILE_ONLY:
            return None
        case _:
            raise ValidationAppError("unknown data access mode", field="access_mode")
