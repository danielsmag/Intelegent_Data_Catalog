FROM ghcr.io/astral-sh/uv:0.13.0-debian AS build

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=3.15

COPY .python-version pyproject.toml uv.lock README.md ./
RUN uv python install 3.15 \
    && uv sync --frozen --no-install-project --no-dev --group docs

COPY src ./src
COPY settings.toml ./
COPY docs ./docs
# declare-assets.rst includes the sibling checkout at ../../../lumen-sales.
COPY --from=lumen-sales spaces /lumen-sales/spaces
COPY --from=lumen-sales scopes /lumen-sales/scopes
COPY --from=lumen-sales assets /lumen-sales/assets
RUN uv sync --frozen --no-dev --group docs \
    && uv run --no-sync sphinx-build -W -b html docs docs/_build/html

FROM nginx:1.29-alpine

COPY docker/docs/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/docs/_build/html /usr/share/nginx/html
