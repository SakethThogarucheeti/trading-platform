FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /workspace/trading-platform

# Dependency layer: only pyproject.toml/uv.lock, so this (heavy — numpy,
# scipy, pyarrow, polars, git-cloned SDKs) is cached and skipped whenever
# only application source changes, not just whenever anything changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project

# App layer: installing the project itself is fast and doesn't re-touch
# the dependency set above.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_HOST=0.0.0.0
ENV UV_NO_SYNC=1

EXPOSE 8081

CMD ["uv", "run", "python", "main.py"]
