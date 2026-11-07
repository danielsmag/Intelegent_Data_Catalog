"""Read and write the markdown files stored beside an agent declaration."""

import re

from intelegent_data_catalog.exceptions import ValidationAppError

_SLUG: re.Pattern[str] = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_MAX_BYTES: int = 32 * 1024


class MarkdownFile:
    """One markdown document split into frontmatter fields and a body."""

    def __init__(self, fields: dict[str, object], body: str) -> None:
        self.fields: dict[str, object] = fields
        self.body: str = body

    def text(self, key: str, default: str = "") -> str:
        """Return a string field."""
        value: object = self.fields.get(key, default)
        if isinstance(value, str):
            return value
        return default

    def flag(self, key: str, default: bool) -> bool:
        """Return a boolean field."""
        value: object = self.fields.get(key, default)
        if isinstance(value, bool):
            return value
        return default

    def number(self, key: str, default: int) -> int:
        """Return an integer field."""
        value: object = self.fields.get(key, default)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return default

    def tools(self) -> list[str]:
        """Return ``metadata.tools`` as strings."""
        metadata: object = self.fields.get("metadata")
        if not isinstance(metadata, dict):
            return []
        raw: object = metadata.get("tools")
        if not isinstance(raw, list):
            return []
        found: list[str] = []
        item: object
        for item in raw:
            if isinstance(item, str) and item.strip() != "":
                found.append(item.strip())
        return found


def check_slug(slug: str) -> str:
    """Reject a slug the harness skill loader would not accept."""
    cleaned: str = slug.strip()
    if _SLUG.fullmatch(cleaned) is None or "--" in cleaned:
        raise ValidationAppError(
            "use lowercase letters, numbers, and single hyphens",
            field="slug",
        )
    return cleaned


def parse_markdown(payload: str) -> MarkdownFile:
    """Split optional YAML frontmatter from the markdown body."""
    if not payload.startswith("---\n"):
        return MarkdownFile({}, payload)
    end: int = payload.find("\n---\n", 4)
    if end < 0:
        raise ValidationAppError("markdown frontmatter is not closed", field="body")
    fields: dict[str, object] = _parse_fields(payload[4:end])
    body: str = payload[end + 5 :]
    if body.startswith("\n"):
        body = body[1:]
    return MarkdownFile(fields, body)


def render_markdown(fields: list[tuple[str, object]], body: str) -> str:
    """Write the frontmatter this catalog stores. Values stay simple."""
    lines: list[str] = ["---"]
    key: str
    value: object
    for key, value in fields:
        lines.extend(_render_field(key, value, 0))
    lines.append("---")
    lines.append("")
    text: str = "\n".join(lines) + body
    if not text.endswith("\n"):
        text = f"{text}\n"
    if len(text.encode()) > _MAX_BYTES:
        raise ValidationAppError("markdown file exceeds 32 KiB", field="body")
    return text


def harness_skill(name: str, description: str, body: str) -> str:
    """Frontmatter ``Skills`` accepts. Catalog fields stay out of this copy."""
    return render_markdown(
        [("name", name), ("description", description)],
        body,
    )


def _parse_fields(raw: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    lines: list[str] = raw.splitlines()
    index: int = 0
    while index < len(lines):
        line: str = lines[index]
        if line.strip() == "":
            index += 1
            continue
        if line.startswith(" ") or ":" not in line:
            raise ValidationAppError("markdown frontmatter is invalid", field="body")
        key: str
        rest: str
        key, _, rest = line.partition(":")
        name: str = key.strip()
        value: str = rest.strip()
        if value == "":
            nested: dict[str, object]
            nested, index = _parse_nested(lines, index + 1)
            fields[name] = nested
            continue
        fields[name] = _scalar(value)
        index += 1
    return fields


def _parse_nested(lines: list[str], index: int) -> tuple[dict[str, object], int]:
    nested: dict[str, object] = {}
    while index < len(lines) and _nested_line(lines[index]):
        line: str = lines[index].strip()
        if ":" not in line:
            raise ValidationAppError("markdown frontmatter is invalid", field="body")
        key: str
        rest: str
        key, _, rest = line.partition(":")
        if rest.strip() == "":
            items: list[str]
            items, index = _parse_list(lines, index + 1)
            nested[key.strip()] = items
            continue
        nested[key.strip()] = _scalar(rest.strip())
        index += 1
    return nested, index


def _nested_line(line: str) -> bool:
    return line.startswith("  ") and not line.startswith("    ")


def _parse_list(lines: list[str], index: int) -> tuple[list[str], int]:
    items: list[str] = []
    while index < len(lines) and lines[index].startswith("    - "):
        parsed: object = _scalar(lines[index].strip()[2:].strip())
        if isinstance(parsed, str):
            items.append(parsed)
        index += 1
    return items, index


def _scalar(value: str) -> object:
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def _render_field(key: str, value: object, indent: int) -> list[str]:
    pad: str = " " * indent
    if isinstance(value, dict):
        lines: list[str] = [f"{pad}{key}:"]
        child_key: object
        child: object
        for child_key, child in value.items():
            lines.extend(_render_field(str(child_key), child, indent + 2))
        return lines
    if isinstance(value, list):
        lines = [f"{pad}{key}:"]
        item: object
        for item in value:
            lines.append(f"{pad}  - {_render_scalar(item)}")
        return lines
    return [f"{pad}{key}: {_render_scalar(value)}"]


def _render_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text: str = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'
