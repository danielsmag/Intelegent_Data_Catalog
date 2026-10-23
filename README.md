# Intelegent Data Catalog

Python 3.15 application for an intelligent data catalog.

The manual is in `docs/`. Build it, then open `docs/_build/html/index.html`.

```bash
uv sync --group docs
uv run sphinx-build -W -b html docs docs/_build/html
```

## Checks

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy
```
