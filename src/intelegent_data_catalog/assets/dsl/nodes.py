"""Space, scope, and asset declarations. Constructing one registers it with the open file."""

import re

from pydantic import JsonValue, ValidationError

from intelegent_data_catalog.assets.definitions.models import AssetSpec, ScopeSpec, SpaceSpec
from intelegent_data_catalog.assets.dsl.collector import current_collector
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators import Operator
from intelegent_data_catalog.persistence.enums import AssetStatus
from intelegent_data_catalog.registry.identifiers import require_slug

_KEY: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,254}$")
_NAME_LIMIT: int = 255


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
    """Declare one asset and the operator that names its source."""

    def __init__(
        self,
        key: str,
        operator: Operator,
        *,
        scope: str | _UnsetType | None = _UNSET,
        space: str | _UnsetType | None = _UNSET,
        name: str | _UnsetType | None = _UNSET,
        description: str | _UnsetType | None = _UNSET,
        context: str | _UnsetType | None = _UNSET,
        status: AssetStatus | _UnsetType | None = _UNSET,
        properties: dict[str, JsonValue] | _UnsetType = _UNSET,
    ) -> None:
        scope_value: str | None
        space_value: str | None
        scope_value, space_value = _target(scope, space)
        status_value: AssetStatus | None = _status(status)
        property_value: dict[str, JsonValue] = _properties(properties)
        try:
            spec: AssetSpec = AssetSpec(
                key=_asset_key(key),
                source=operator.to_source(),
                scope=scope_value,
                space=space_value,
                name=_optional_text(name, "name", required=True),
                description=_optional_text(description, "description", required=False),
                context=_optional_text(context, "context", required=False),
                status=status_value,
                properties=property_value,
                set_fields=_set_fields(
                    (
                        ("name", name),
                        ("description", description),
                        ("context", context),
                        ("status", status),
                        ("properties", properties),
                    ),
                ),
            )
        except ValidationError as err:
            raise AssetDefinitionError("invalid asset") from err
        current_collector().add_asset(spec)


def _target(
    scope: str | _UnsetType | None,
    space: str | _UnsetType | None,
) -> tuple[str | None, str | None]:
    scope_set: bool = not isinstance(scope, _UnsetType)
    space_set: bool = not isinstance(space, _UnsetType)
    if scope_set == space_set:
        raise AssetDefinitionError("asset needs exactly one of scope or space")
    if scope_set:
        if not isinstance(scope, str):
            raise AssetDefinitionError("scope cannot be null")
        return _scope_ref(scope), None
    if not isinstance(space, str):
        raise AssetDefinitionError("space cannot be null")
    return None, _slug(space, "space")


def _scope_ref(value: str) -> str:
    space_slug: str
    scope_slug: str
    space_slug, separator, scope_slug = value.partition("/")
    if separator != "/" or space_slug == "" or scope_slug == "" or "/" in scope_slug:
        raise AssetDefinitionError("scope must be space-slug/scope-slug")
    return f"{_slug(space_slug, 'scope')}/{_slug(scope_slug, 'scope')}"


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


def _set_fields(pairs: tuple[tuple[str, object], ...]) -> list[str]:
    found: list[str] = []
    for name, value in pairs:
        if not isinstance(value, _UnsetType):
            found.append(name)
    return found
