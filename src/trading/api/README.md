# api/

External-facing interfaces: HTTP API endpoints, the dashboard server, and Telegram alerting.

## Structure

```
api/
├── routers/             # FastAPI route modules — one file per API domain
│   ├── _helpers.py      # shared session_filter helper
│   ├── _middleware.py   # RequestIdMiddleware, AccessLogMiddleware
│   ├── auth.py          # /api/auth/*
│   ├── market.py        # /api/ping, /api/health, /api/positions, /api/signals, /api/candles, /api/ticks
│   ├── algos.py         # /api/algos*
│   ├── pnl.py           # /api/pnl, /api/pnl/by-algo
│   ├── reports.py       # /api/reports/*
│   ├── charts.py        # /api/charts
│   ├── stream.py        # /api/decisions/stream (SSE)
│   ├── broker.py        # /api/postback (Zerodha webhook)
│   └── data.py          # /api/sessions, /api/settings, /api/instruments, /api/trades, /api/candles/history
├── telegram.py          # TelegramAlerter — async Telegram Bot API client
└── dashboard/
    ├── app.py           # build_app() — assembles all routers into a FastAPI app
    └── component.py     # DashboardServer — Component wrapper (starts uvicorn)
```

## HTTP API

The routers in `api/routers/` are consumed by `dashboard/app.py` today but are structured to be composable — any server can import and mount individual routers.

Each router module exports a `create_<module>_router(...)` factory that accepts only its own dependencies and returns a configured `APIRouter`.

See `dashboard/README.md` for the full endpoint list and middleware details.

## Dashboard server

When `DASHBOARD_ENABLED=true`, a FastAPI app serves on `DASHBOARD_HOST:DASHBOARD_PORT` (default `127.0.0.1:8081`).

`DashboardServer` participates in the `Runtime` lifecycle — it starts last (so all other components are running when requests arrive) and shuts down gracefully on stop.

## Telegram alerts

`TelegramAlerter` sends messages to a configured chat ID via the Telegram Bot API. It is passed as the alerter callback to `HeartbeatMonitor` in `monitoring/`.

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Leave both empty to disable alerting without changing any other code.
