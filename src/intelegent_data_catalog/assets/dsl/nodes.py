"""Asset declarations. Constructing one registers it with the open file."""

import re

from pydantic import ValidationError

from intelegent_data_catalog.assets.definitions.models import AssetSpec
from intelegent_data_catalog.assets.dsl.collector import current_collector
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.base import Operator
from intelegent_data_catalog.assets.dsl.sources import (
    AirflowInstance,
    GitHubRepo,
    IcebergSchema,
    IcebergTable,
    Source,
)
from intelegent_data_catalog.schedual.cron import ScheduleError, parse_schedule

_KEY: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,254}$")
_TAG: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_TAG_LIMIT: int = 32
_ALLOWED: dict[type[Source], frozenset[str]] = {
    AirflowInstance: frozenset({"AirflowMetadata"}),
    IcebergTable: frozenset({"IcebergTableMetadata", "IcebergTableProfile"}),
    IcebergSchema: frozenset({"IcebergSchemaMetadata", "IcebergSchemaProfile"}),
    GitHubRepo: frozenset({"GitHubRepoSnapshot"}),
}


class _UnsetType:
    """Marks an argument the caller did not pass."""


_UNSET: _UnsetType = _UnsetType()


class Asset:
    """Declare one asset, the source it reads, and the deterministic steps that run.

    Assign the object to ``asset``.
    ``key`` is the stable id and the visible name.
    ``operators`` run in order. Omit them and the asset is resolved but nothing runs.
    ``schedule`` is a five-field cron. Omit it to run once when the asset is first applied.
    A GitHub asset scheduled this way reruns when the branch head commit changes.
    """

    def __init__(
        self,
        key: str,
        source: Source,
        *,
        operators: list[Operator] | None = None,
        description: str | _UnsetType | None = _UNSET,
        tags: list[str] | _UnsetType | None = _UNSET,
        schedule: str | _UnsetType | None = _UNSET,
    ) -> None:
        steps: list[Operator] = [] if operators is None else list(operators)
        _check_steps(source, steps)
        try:
            spec: AssetSpec = AssetSpec(
                key=_asset_key(key),
                source=source.to_ref(),
                operators=[step.to_ref() for step in steps],
                schedule=_schedule(schedule),
                description=_optional_text(description),
                tags=_tags(tags),
                set_fields=_set_fields((("description", description), ("tags", tags))),
            )
        except ValidationError as err:
            raise AssetDefinitionError("invalid asset") from err
        current_collector().add_asset(spec)


def _check_steps(source: Source, steps: list[Operator]) -> None:
    allowed: frozenset[str] | None = _ALLOWED.get(type(source))
    if allowed is None:
        raise AssetDefinitionError(f"unsupported source {type(source).__name__}")
    seen: set[str] = set()
    step: Operator
    for step in steps:
        if step.task_id in seen:
            raise AssetDefinitionError(f"operator {step.task_id} is listed twice")
        seen.add(step.task_id)
        if type(step).__name__ not in allowed:
            raise AssetDefinitionError(
                f"{type(source).__name__} does not allow {type(step).__name__}",
            )


def _schedule(value: str | _UnsetType | None) -> str | None:
    if isinstance(value, _UnsetType) or value is None:
        return None
    if not isinstance(value, str):
        raise AssetDefinitionError("schedule must be a cron string")
    try:
        return parse_schedule(value)
    except ScheduleError as err:
        raise AssetDefinitionError(str(err)) from err


def _asset_key(value: str) -> str:
    if _KEY.fullmatch(value) is None:
        raise AssetDefinitionError(
            "asset key must start with a letter or digit and use letters, digits, '_' or '-'",
        )
    return value


def _optional_text(value: object) -> str | None:
    if isinstance(value, _UnsetType) or value is None:
        return None
    if not isinstance(value, str):
        raise AssetDefinitionError("description must be a string")
    cleaned: str = value.strip()
    if cleaned == "":
        return None
    return cleaned


def _tags(value: list[str] | _UnsetType | None) -> list[str]:
    if isinstance(value, _UnsetType) or value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AssetDefinitionError("tags must be a list of strings")
    found: list[str] = []
    seen: set[str] = set()
    item: object
    for item in value:
        if not isinstance(item, str):
            raise AssetDefinitionError("tags must be a list of strings")
        cleaned: str = item.strip()
        if _TAG.fullmatch(cleaned) is None:
            raise AssetDefinitionError(
                "each tag must start with a letter or digit and use letters, digits, "
                "spaces, '_', '-' or '.'",
            )
        if cleaned in seen:
            continue
        seen.add(cleaned)
        found.append(cleaned)
    if len(found) > _TAG_LIMIT:
        raise AssetDefinitionError(f"tags cannot list more than {_TAG_LIMIT} values")
    return found


def _set_fields(pairs: tuple[tuple[str, object], ...]) -> list[str]:
    found: list[str] = []
    for name, value in pairs:
        if not isinstance(value, _UnsetType):
            found.append(name)
    return found
