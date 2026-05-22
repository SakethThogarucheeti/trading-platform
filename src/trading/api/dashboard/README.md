# api/dashboard/

FastAPI read-only dashboard server, run as a `Component` inside the `Runtime`.

## Files

| File | Purpose |
|------|---------|
| `app.py` | `build_app()` — FastAPI app factory with all dashboard endpoints |
| `component.py` | `DashboardServer` — `Component` that runs uvicorn in the `Runtime` |

## Endpoints

All endpoints are read-only (no DB writes). They query the shared `async_sessionmaker`.

| Endpoint | Description |
|----------|-------------|
| `GET /api/ping` | Health check |
| `GET /api/auth/login-url` | Returns Zerodha OAuth URL |
| `GET /api/auth/callback` | Handles Zerodha OAuth callback, stores token |
| `GET /api/sessions` | Lists backtest and live trading sessions |
| `GET /api/positions` | Current open positions |
| `GET /api/health` | Module heartbeat statuses |
| `GET /api/signals` | Recent signals (filterable by session_id) |
| `GET /api/candles` | OHLCV candle data |
| `GET /api/ticks` | Raw tick log |
| `GET /api/pnl` | Realized P&L summary |
| `GET /api/pnl/by-algo` | P&L broken down per algo |
| `GET /api/charts` | Indicator series for charting |
| `GET /api/decisions/stream` | SSE stream of live decision events |
| `GET /api/reports/sessions` | Past report sessions |
| `GET /api/reports/live` | Live report |
| `GET /api/reports/{session_id}` | Report for a specific session |

## DashboardServer

`DashboardServer` is started last in the component list so all other components are running before the server begins accepting requests. It wraps uvicorn's `Server` class and signals exit by setting `server.should_exit = True` on shutdown.

Default bind: `127.0.0.1:8081` (configurable via `Settings.dashboard_host` / `Settings.dashboard_port`).

## Relationship to other packages

- `core/lifecycle/component.py` — `DashboardServer` extends `Component`
- `storage/stores/` — all store types queried by dashboard endpoints
- `reports/` — report generation endpoints delegate to `reports/engine.py`
- `di/providers/components.py` — conditionally provides `DashboardServer` when `settings.dashboard_enabled`
