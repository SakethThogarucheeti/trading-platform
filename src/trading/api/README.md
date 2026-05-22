# api/

External-facing interfaces: the read-only HTTP dashboard and Telegram alerting.

## Structure

```
api/
├── telegram.py          # TelegramAlerter — async Telegram Bot API client
└── dashboard/
    ├── app.py           # FastAPI app factory — all /api/* endpoints
    ├── component.py     # DashboardServer — Component wrapper (starts uvicorn)
    └── static/          # HTML + JS frontend
```

## Dashboard

When `DASHBOARD_ENABLED=true`, a FastAPI app serves on `DASHBOARD_HOST:DASHBOARD_PORT` (default `127.0.0.1:8081`).

All endpoints are **read-only** — the dashboard never writes to the database or sends orders.

See `dashboard/README.md` for the full endpoint list.

`DashboardServer` participates in the `Runtime` lifecycle — it starts last (so all other components are running when requests arrive) and shuts down gracefully on stop.

## Telegram alerts

`TelegramAlerter` sends messages to a configured chat ID via the Telegram Bot API. It is passed as the alerter callback to `HeartbeatMonitor` in `monitoring/`, which calls it when any module's heartbeat goes stale.

Configuration:

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id   # or group chat ID (negative number)
```

Leave both empty to disable alerting without changing any other code.

## Relationship to other packages

- `monitoring/heartbeat.py` — receives `TelegramAlerter` as its alerter callback
- `dashboard/` — see its own README for full details
