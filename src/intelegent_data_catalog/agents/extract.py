"""Turn file and notebook bytes into text the file agent can read."""

import json
import re
from html.parser import HTMLParser

_TEXT_SUFFIXES: frozenset[str] = frozenset(
    {"md", "txt", "py", "sql", "json", "yaml", "yml", "html", "htm", "csv", "tsv", "xml"},
)
_TABLE_REF: re.Pattern[str] = re.compile(
    r"""(?:spark\.table|read\.table|FROM|JOIN)\s*\(?\s*['\"]([A-Za-z_][\w.]*)['\"]""",
    re.IGNORECASE,
)
_QUALIFIED: re.Pattern[str] = re.compile(
    r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b",
)
_MAX_CHARS: int = 20_000


class _TextExtractor(HTMLParser):
    """Collects visible HTML text. Tags are dropped."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned: str = data.strip()
        if cleaned != "":
            self.parts.append(cleaned)


class FileText:
    """Extracted document plus table names mentioned in it."""

    def __init__(self, text: str, referenced_tables: list[str]) -> None:
        self.text: str = text
        self.referenced_tables: list[str] = referenced_tables


def extract_document(
    name: str,
    content_type: str | None,
    payload: bytes,
    *,
    include_outputs: bool,
) -> FileText:
    """Read text from a file. Images contribute their name only."""
    if name.lower().endswith(".ipynb") or (content_type or "").endswith("ipynb+json"):
        text: str = _notebook(payload, include_outputs)
    elif _is_html(name, content_type):
        text = _html(payload)
    elif _is_text(name, content_type):
        text = _decode(payload)
    else:
        text = ""
    clipped: str = text[:_MAX_CHARS]
    return FileText(clipped, referenced_table_names(clipped))


def referenced_table_names(text: str) -> list[str]:
    """Table names named in SQL, ``spark.table`` calls, or catalog.namespace.table paths."""
    found: list[str] = []
    seen: set[str] = set()
    match: re.Match[str]
    for match in _TABLE_REF.finditer(text):
        _add_name(found, seen, match.group(1).split(".")[-1])
    for match in _QUALIFIED.finditer(text):
        _add_name(found, seen, match.group(3))
    return found


def _notebook(payload: bytes, include_outputs: bool) -> str:
    try:
        raw: object = json.loads(_decode(payload) or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(raw, dict):
        return ""
    cells: object = raw.get("cells")
    if not isinstance(cells, list):
        return ""
    parts: list[str] = []
    cell: object
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        source: str = _cell_source(cell.get("source"))
        if source.strip() != "":
            parts.append(source)
        if include_outputs:
            parts.extend(_outputs(cell.get("outputs")))
    return "\n\n".join(parts)


def _cell_source(source: object) -> str:
    if isinstance(source, str):
        return source
    if isinstance(source, list):
        chunks: list[str] = []
        item: object
        for item in source:
            if isinstance(item, str):
                chunks.append(item)
        return "".join(chunks)
    return ""


def _outputs(outputs: object) -> list[str]:
    if not isinstance(outputs, list):
        return []
    parts: list[str] = []
    output: object
    for output in outputs:
        if not isinstance(output, dict):
            continue
        text: object = output.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif isinstance(text, list):
            parts.append(_cell_source(text))
    return parts


def _html(payload: bytes) -> str:
    parser: _TextExtractor = _TextExtractor()
    parser.feed(_decode(payload))
    return " ".join(parser.parts)


def _decode(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


def _is_html(name: str, content_type: str | None) -> bool:
    if name.lower().endswith((".html", ".htm")):
        return True
    return (content_type or "").startswith("text/html")


def _is_text(name: str, content_type: str | None) -> bool:
    suffix: str = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix in _TEXT_SUFFIXES:
        return True
    media: str = content_type or ""
    return media.startswith("text/") or media in {"application/json", "application/xml"}


def _add_name(found: list[str], seen: set[str], name: str) -> None:
    key: str = name.casefold()
    if key == "" or key in seen:
        return
    seen.add(key)
    found.append(name)
