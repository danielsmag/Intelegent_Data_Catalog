.PHONY: help build up down rebuild logs test lint typecheck docs docs-up

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
		'docs        build the Sphinx HTML manual' \
		'docs-up     serve the manual at http://localhost:8000'

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

docs:
	uv sync --group docs
	uv run sphinx-build -W -b html docs docs/_build/html

docs-up:
	$(COMPOSE) up -d --build catalog-docs
