"""Sphinx configuration for the catalog manual."""

from __future__ import annotations

import json
import sys
import warnings
from collections.abc import Iterator
from importlib.metadata import version as package_version
from pathlib import Path

from fastapi import FastAPI
from sphinx.application import Sphinx

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API",
    category=UserWarning,
)

_DOCS_DIR: Path = Path(__file__).resolve().parent
_SRC_DIR: Path = _DOCS_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import sphinxcontrib.redoc as redoc_extension  # noqa: E402

from intelegent_data_catalog.api.app import configure_app  # noqa: E402

_HIDDEN_MEMBERS: tuple[str, ...] = (
    "model_config",
    "model_fields",
    "model_computed_fields",
    "model_extra",
    "model_fields_set",
    "model_construct",
    "model_copy",
    "model_dump",
    "model_dump_json",
    "model_json_schema",
    "model_parametrized_name",
    "model_post_init",
    "model_rebuild",
    "model_validate",
    "model_validate_json",
    "model_validate_strings",
    "construct",
    "copy",
    "dict",
    "json",
    "parse_obj",
    "parse_raw",
    "schema",
    "schema_json",
    "update_forward_refs",
    "from_orm",
)


class OpenApiExporter:
    """Write the v1 schema from the router, without opening the catalog database."""

    def __init__(self, destination: Path, api_version: str) -> None:
        self._destination: Path = destination
        self._api_version: str = api_version

    def write(self) -> None:
        """Create ``openapi.json`` for the ReDoc page."""
        app: FastAPI = FastAPI(
            title="Intelegent Data Catalog",
            version=self._api_version,
        )
        configure_app(app)
        schema: object = app.openapi()
        self._destination.parent.mkdir(parents=True, exist_ok=True)
        body: str = json.dumps(schema, indent=2)
        self._destination.write_text(f"{body}\n", encoding="utf-8")


def _collect_redoc_pages(app: Sphinx) -> Iterator[tuple[str, dict[str, str], str]]:
    """Yield the ReDoc page.

    sphinxcontrib-redoc 1.6.0 yields a Jinja template object. Sphinx 9 renders
    by template name, so the manual replaces that collector and keeps the
    extension's ``redoc.js`` copy.
    """
    spec_path: Path = Path(app.confdir) / "_static" / "openapi.json"
    raw: str = spec_path.read_text(encoding="utf-8")
    safe: str = raw.replace("<", "\\u003c")
    context: dict[str, str] = {
        "title": "Intelegent Data Catalog API",
        "spec_json": safe,
    }
    yield "reference/openapi", context, "redoc.html"


redoc_extension.render = _collect_redoc_pages

project: str = "Intelegent Data Catalog"
author: str = "Intelegent Data Catalog"
copyright: str = "2026, Intelegent Data Catalog"
release: str = package_version("intelegent-data-catalog")
version: str = release

OpenApiExporter(_DOCS_DIR / "_static" / "openapi.json", release).write()

extensions: list[str] = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinxcontrib.redoc",
]
templates_path: list[str] = ["_templates"]
exclude_patterns: list[str] = ["_build"]
html_theme: str = "pydata_sphinx_theme"
html_static_path: list[str] = ["_static"]
html_title: str = "Intelegent Data Catalog"
html_css_files: list[str] = ["catalog.css"]
html_context: dict[str, str] = {"default_mode": "dark"}
html_theme_options: dict[str, object] = {
    "show_nav_level": 2,
    "collapse_navigation": False,
    "show_prev_next": True,
    "navigation_depth": 3,
    "pygments_light_style": "monokai",
    "pygments_dark_style": "monokai",
    "navbar_end": [],
    "footer_start": ["copyright"],
    "footer_end": [],
    "secondary_sidebar_items": ["page-toc"],
    "logo": {
        "text": "Intelegent Data Catalog",
        "image_light": "_static/mark.svg",
        "image_dark": "_static/mark.svg",
    },
}
autosummary_generate: bool = True
autosummary_imported_members: bool = True
autodoc_member_order: str = "bysource"
autodoc_typehints: str = "signature"
autodoc_default_options: dict[str, bool | str] = {
    "members": True,
    "show-inheritance": True,
    "special-members": "__init__",
    "exclude-members": ",".join(_HIDDEN_MEMBERS),
}
