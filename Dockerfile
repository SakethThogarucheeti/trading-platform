FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /workspace/trading-platform

COPY . .

RUN uv sync --frozen --no-dev --no-editable

ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_HOST=0.0.0.0
ENV UV_NO_SYNC=1

EXPOSE 8081

CMD ["uv", "run", "python", "main.py"]
