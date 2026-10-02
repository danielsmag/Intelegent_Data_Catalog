"""Slug and identifier rules for catalog records."""

import re

_SLUG: re.Pattern[str] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(name: str) -> str:
    """Build a slug from a display name. Letters are lowercased; other characters stay out."""
    slug: str = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if _SLUG.fullmatch(slug) is None:
        raise ValueError("name must contain a letter or digit to form a slug")
    return slug


def require_slug(slug: str) -> str:
    """Accept a slug that is already lowercase and hyphenated."""
    if _SLUG.fullmatch(slug) is None:
        raise ValueError("slug must be lowercase letters, digits, and single hyphens")
    return slug


def exact_identifier(value: str) -> str:
    """Keep a case-sensitive identifier. Reject blank and surrounding whitespace."""
    if value.strip() == "" or value != value.strip():
        raise ValueError("identifier must be non-blank and preserve case")
    return value


def source_key_from_parts(catalog: str, namespace: str, table: str) -> str:
    """Build the default source key without changing identifier case."""
    return f"{catalog}.{namespace}.{table}"
