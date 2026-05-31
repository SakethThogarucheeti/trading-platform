# api/routers/

FastAPI router modules — one file per API domain. Each module exports a `create_<module>_router(...)` factory that accepts only the dependencies it needs and returns a configured `APIRouter`.

## Modules

| File | Endpoints | Dependencies |
|------|-----------|--------------|
| `auth.py` | `/api/auth/login-url`, `/api/auth/callback` | `session_factory`, Zerodha credentials, `kite_client`, `kite_ingestor` |
| `market.py` | `/api/ping`, `/api/health`, `/api/positions`, `/api/signals`, `/api/candles`, `/api/ticks` | `session_factory`, `clock`, `heartbeat_stale_secs` |
| `algos.py` | `/api/algos` (GET/PATCH), `/api/algos/{name}/reset-state` | `session_factory` |
| `pnl.py` | `/api/pnl`, `/api/pnl/by-algo` | `session_factory`, `clock`, `cacher_factory` |
| `reports.py` | `/api/reports/sessions`, `/api/reports/live`, `/api/reports/{session_id}` | `results_dir`, `session_factory`, `clock`, `cacher_factory` |
| `charts.py` | `/api/charts` | `session_factory`, `clock` |
| `stream.py` | `/api/decisions/stream` (SSE) | `session_factory`, `clock` |
| `broker.py` | `/api/postback` | `order_executor` |
| `data.py` | `/api/sessions`, `/api/settings`, `/api/instruments`, `/api/trades`, `/api/candles/history` | `session_factory`, `clock`, `candle_intervals`, `historical_data_service` |

## Shared utilities

| File | Purpose |
|------|---------|
| `_helpers.py` | `session_filter()` — WHERE clause for filtering `DecisionLog` by `session_id` (used by `market` and `stream`) |
| `_middleware.py` | `RequestIdMiddleware`, `AccessLogMiddleware` — ASGI middleware classes used by the dashboard server |

## Adding a new router

1. Create `api/routers/<module>.py` with a `create_<module>_router(...)` factory.
2. Import and wire it in `api/dashboard/app.py` via `app.include_router(create_<module>_router(...))`.
