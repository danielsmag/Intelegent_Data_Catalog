"""Shared checks for registry aggregate services."""

from uuid import UUID

from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.persistence.enums import ContextOrigin
from intelegent_data_catalog.persistence.models import (
    AssetRow,
    CatalogTableRow,
    ColumnRow,
)
from intelegent_data_catalog.registry.identifiers import slugify


def required[T](value: T | None, field: str) -> T:
    if value is None:
        raise ValidationAppError(f"{field} cannot be null", field=field)
    return value


def apply_user_context(
    row: CatalogTableRow | ColumnRow | AssetRow,
    value: str | None,
) -> None:
    """Store a manual context edit. Blank text clears both the text and the origin."""
    text: str | None = None if value is None else value.strip()
    if text == "":
        text = None
    row.context = text
    row.context_origin = None if text is None else ContextOrigin.USER.value


def slug_for(name: str, explicit: str | None) -> str:
    try:
        if explicit is not None:
            return explicit
        return slugify(name)
    except ValueError as err:
        raise ValidationAppError(str(err), field="slug") from err


def slug_after_patch(
    name: str,
    changes: dict[str, object],
    explicit_slug: str | None,
) -> str | None:
    """Slug to store after a patch. None leaves the current slug in place."""
    if "slug" in changes:
        return required(explicit_slug, "slug")
    if "name" in changes:
        return slug_for(name, None)
    return None


def require_row[RowT](row: RowT | None, entity: str, entity_id: UUID) -> RowT:
    if row is None:
        raise EntityNotFoundError(entity, str(entity_id))
    return row
