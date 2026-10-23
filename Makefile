.PHONY: help build up down rebuild logs test lint typecheck load-assets docs

COMPOSE ?= docker compose

help:
	@printf '%s\n' \
		'build       docker compose build' \
		'up          docker compose up -d --build' \
		'rebuild     docker compose up -d --build --force-recreate' \
		'down        docker compose down' \
		'logs        follow spark-lineage logs' \
		'test        uv run pytest' \
		'lint        uv run ruff check .' \
		'typecheck   uv run mypy' \
		'load-assets copy examples/asset-definitions to S3' \
		'docs        build the Sphinx HTML manual'

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d --build

rebuild:
	$(COMPOSE) up -d --build --force-recreate

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f spark-lineage

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy

load-assets:
	uv run python scripts/load_asset_definitions.py

docs:
	uv sync --group docs
	uv run sphinx-build -W -b html docs docs/_build/html
