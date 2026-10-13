FROM ghcr.io/astral-sh/uv:0.13.0-debian

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=3.15

COPY .python-version pyproject.toml uv.lock README.md ./
RUN uv python install 3.15 \
    && uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev

COPY settings.toml ./

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8090
CMD ["intelegent-data-catalog-api"]
