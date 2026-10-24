"""Space, scope, and asset declarations. Constructing one registers it with the open file."""

import re

from pydantic import JsonValue, ValidationError

from intelegent_data_catalog.assets.definitions.models import AssetSpec, ScopeSpec, SpaceSpec
from intelegent_data_catalog.assets.dsl.collector import current_collector
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators import Operator
from intelegent_data_catalog.assets.execution.cron import parse_schedule
from intelegent_data_catalog.persistence.enums import AssetStatus
from intelegent_data_catalog.registry.identifiers import require_slug

_KEY: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,254}$")
_TAG: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_NAME_LIMIT: int = 255
_TAG_LIMIT: int = 32


class _UnsetType:
    """Marks an argument the caller did not pass."""


_UNSET: _UnsetType = _UnsetType()


class Space:
    """Declare a space. Missing spaces are created. Existing spaces are patched."""

    def __init__(
        self,
        slug: str,
        *,
        name: str | _UnsetType | None = _UNSET,
        description: str | _UnsetType | None = _UNSET,
        context: str | _UnsetType | None = _UNSET,
    ) -> None:
        checked: str = _slug(slug, "slug")
        spec: SpaceSpec = SpaceSpec(
            slug=checked,
            name=_optional_text(name, "name", required=True),
            description=_optional_text(description, "description", required=False),
            context=_optional_text(context, "context", required=False),
            set_fields=_set_fields(
                (("name", name), ("description", description), ("context", context)),
            ),
        )
        current_collector().add_space(spec)


class Scope:
    """Declare a scope inside a space slug."""

    def __init__(
        self,
        slug: str,
        *,
        space: str,
        name: str | _UnsetType | None = _UNSET,
        description: str | _UnsetType | None = _UNSET,
        context: str | _UnsetType | None = _UNSET,
    ) -> None:
        spec: ScopeSpec = ScopeSpec(
            slug=_slug(slug, "slug"),
            space_slug=_slug(space, "space"),
            name=_optional_text(name, "name", required=True),
            description=_optional_text(description, "description", required=False),
            context=_optional_text(context, "context", required=False),
            set_fields=_set_fields(
                (("name", name), ("description", description), ("context", context)),
            ),
        )
        current_collector().add_scope(spec)


class Asset:
    """Declare one asset and the operator that names its source and builds its context.

    Assign the object to ``asset``. The asset is not placed in a space or scope.
    ``key`` is the stable id and the visible name. There is no separate name argument.
    ``schedule`` is a five-field cron; omit it to run once when the asset is first applied.
    ``context`` is a manual override. Without it, the operator writes the context.
    ``tags`` is a list of labels. Omit it and the stored tags stay. Pass ``[]`` to clear them.
    """

    def __init__(
        self,
        key: str,
        operator: Operator,
        *,
        description: str | _UnsetType | None = _UNSET,
        context: str | _UnsetType | None = _UNSET,
        status: AssetStatus | _UnsetType | None = _UNSET,
        properties: dict[str, JsonValue] | _UnsetType = _UNSET,
        tags: list[str] | _UnsetType | None = _UNSET,
        schedule: str | _UnsetType | None = _UNSET,
    ) -> None:
        status_value: AssetStatus | None = _status(status)
        property_value: dict[str, JsonValue] = _properties(properties)
        tag_value: list[str] = _tags(tags)
        schedule_value: str | None = _schedule(schedule)
        try:
            spec: AssetSpec = AssetSpec(
                key=_asset_key(key),
                source=operator.to_source(),
                operator=operator.to_ref(),
                schedule=schedule_value,
                description=_optional_text(description, "description", required=False),
                context=_optional_text(context, "context", required=False),
                status=status_value,
                properties=property_value,
                tags=tag_value,
                set_fields=_set_fields(
                    (
                        ("description", description),
                        ("context", context),
                        ("status", status),
                        ("properties", properties),
                        ("tags", tags),
                    ),
                ),
            )
        except ValidationError as err:
            raise AssetDefinitionError("invalid asset") from err
        current_collector().add_asset(spec)


def _schedule(value: str | _UnsetType | None) -> str | None:
    if isinstance(value, _UnsetType) or value is None:
        return None
    if not isinstance(value, str):
        raise AssetDefinitionError("schedule must be a cron string")
    return parse_schedule(value)


def _slug(value: str, field: str) -> str:
    try:
        return require_slug(value)
    except ValueError as err:
        raise AssetDefinitionError(f"{field}: {err}") from err


def _asset_key(value: str) -> str:
    if _KEY.fullmatch(value) is None:
        raise AssetDefinitionError(
            "asset key must start with a letter or digit and use letters, digits, '_' or '-'",
        )
    return value


def _optional_text(value: object, field: str, *, required: bool) -> str | None:
    if isinstance(value, _UnsetType):
        return None
    if value is None:
        if required:
            raise AssetDefinitionError(f"{field} cannot be null")
        return None
    if not isinstance(value, str):
        raise AssetDefinitionError(f"{field} must be a string")
    cleaned: str = value.strip()
    if cleaned == "":
        if required:
            raise AssetDefinitionError(f"{field} cannot be blank")
        return None
    if field == "name" and len(cleaned) > _NAME_LIMIT:
        raise AssetDefinitionError(f"{field} is longer than {_NAME_LIMIT} characters")
    return cleaned


def _status(value: AssetStatus | _UnsetType | None) -> AssetStatus | None:
    if isinstance(value, _UnsetType):
        return None
    if not isinstance(value, AssetStatus):
        raise AssetDefinitionError("status must be an asset status")
    return value


def _properties(value: dict[str, JsonValue] | _UnsetType) -> dict[str, JsonValue]:
    if isinstance(value, _UnsetType):
        return {}
    return dict(value)


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
