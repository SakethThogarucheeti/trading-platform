FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /workspace/trading-platform

COPY . .

RUN uv sync --frozen --no-dev --no-editable

ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_HOST=0.0.0.0
ENV UV_NO_SYNC=1

EXPOSE 8081

CMD ["uv", "run", "python", "main.py"]
