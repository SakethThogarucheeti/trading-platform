FROM python:3.13-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Build context is the repo root (docker-compose sets context: .)
WORKDIR /workspace/trading-platform

# Copy local editable dependency first (needed by uv sync)
COPY quantindicators/ /workspace/quantindicators/

# Copy the full source before syncing so uv can build the trading-platform wheel
COPY trading-platform/ .

# Install all production deps + the trading-platform package itself as wheels.
# UV_NO_SYNC=1 at runtime prevents uv run from re-syncing on startup.
RUN uv sync --frozen --no-dev --no-editable

ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_HOST=0.0.0.0
# Prevent uv run from checking/updating the environment at startup
ENV UV_NO_SYNC=1

EXPOSE 8081

CMD ["uv", "run", "python", "main.py"]
