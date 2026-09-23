# Intelegent Data Catalog

Python 3.14 application for an intelligent data catalog.

## Setup

```bash
uv sync --group dev
```

## Run

```bash
uv run intelegent-data-catalog
```

Settings use the `CATALOG_` environment prefix (`CATALOG_APP_NAME`, `CATALOG_DEBUG`).

## Neo4j

```bash
cp .env.example .env
docker compose up -d
```

| Surface | Address |
| --- | --- |
| Browser | http://localhost:7474 |
| Bolt | bolt://localhost:7687 |

Login uses `NEO4J_USER` and `NEO4J_PASSWORD` from `.env`. Defaults are `neo4j` / `password`. Data stays in the `neo4j_data` volume.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run mypy
```
