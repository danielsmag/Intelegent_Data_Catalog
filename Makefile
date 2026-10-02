.PHONY: help build up down logs test lint typecheck

COMPOSE ?= docker compose

help:
	@printf '%s\n' \
		'build       docker compose build' \
		'up          docker compose up -d --build' \
		'down        docker compose down' \
		'logs        follow spark-lineage logs' \
		'test        uv run pytest' \
		'lint        uv run ruff check .' \
		'typecheck   uv run mypy'

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d --build

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
