# Intelegent Data Catalog

Python 3.15 application for an intelligent data catalog.

The manual is in `docs/`. Compose serves it at <http://localhost:8000>. Build it locally, then open `docs/_build/html/index.html`.

```bash
docker compose up -d --build catalog-docs
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
