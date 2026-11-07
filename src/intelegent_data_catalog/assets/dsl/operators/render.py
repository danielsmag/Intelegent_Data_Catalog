"""Small markdown builders shared by deterministic operators."""

import json
from typing import cast

from pydantic import BaseModel, JsonValue


def heading(level: int, text: str) -> str:
    """Return one ATX heading."""
    marks: str = "#" * level
    return f"{marks} {text}"


def bullet_list(items: list[str]) -> str:
    """Return a markdown list. An empty list becomes a dash."""
    if len(items) == 0:
        return "- (none)"
    return "\n".join(f"- {item}" for item in items)


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Return a GitHub-flavored markdown table."""
    head: str = "| " + " | ".join(_cell(header) for header in headers) + " |"
    rule: str = "| " + " | ".join("---" for _header in headers) + " |"
    body: list[str] = [
        "| " + " | ".join(_cell(cell) for cell in row) + " |"
        for row in rows
    ]
    if len(body) == 0:
        body.append("| " + " | ".join("" for _header in headers) + " |")
    return "\n".join([head, rule, *body])


def fence(language: str, body: str) -> str:
    """Return a fenced code block."""
    return f"```{language}\n{body}\n```"


def json_block(data: JsonValue) -> str:
    """Pretty-print JSON inside a fence."""
    text: str = json.dumps(data, indent=2, sort_keys=True, default=str)
    return fence("json", text)


def as_json(value: object) -> JsonValue:
    """Return ``value`` as a JSON value mypy accepts."""
    raw: object = json.loads(json.dumps(value, default=str))
    return cast(JsonValue, raw)


def model_json(model: BaseModel) -> JsonValue:
    """Return one model's JSON value."""
    dumped: object = model.model_dump(mode="json")
    if isinstance(dumped, (dict, list, str, int, float, bool)) or dumped is None:
        return dumped
    return str(dumped)


def join_sections(parts: list[str]) -> str:
    """Join non-blank sections with a blank line."""
    kept: list[str] = [part.strip() for part in parts if part.strip() != ""]
    return "\n\n".join(kept) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
