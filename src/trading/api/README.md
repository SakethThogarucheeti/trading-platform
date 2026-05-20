# monitoring

Live portfolio visibility and out-of-band alerts. Exposes a read-only HTTP dashboard and sends Telegram notifications for risk events.

## Structure

```
monitoring/
├── telegram.py          # TelegramAlerter — async Telegram Bot API client
└── dashboard/
    ├── app.py           # FastAPI app factory — all /api/* endpoints
    ├── component.py     # DashboardComponent — Component wrapper (starts uvicorn)
    └── static/          # HTML + JS frontend
```

## Dashboard

When `DASHBOARD_ENABLED=true`, a FastAPI app serves on `DASHBOARD_HOST:DASHBOARD_PORT` (default `127.0.0.1:8081`).

All endpoints are **read-only** — the dashboard never writes to the database or sends orders.

| Endpoint | Description |
|----------|-------------|
| `GET /api/positions` | Current net positions |
| `GET /api/orders` | Order history with status |
| `GET /api/signals` | Generated signals |
| `GET /api/decision-log` | Full pipeline audit trail (filterable by `tick_log_id`) |
| `GET /api/sessions` | List of trading sessions |
| `GET /api/algo-state` | Per-strategy live state snapshot |
| `GET /health` | Liveness probe |

The `DashboardComponent` starts a `uvicorn` server in `_setup()` and shuts it down in `_teardown()`, so it participates in the ordered `Runtime` lifecycle.

## Telegram alerts

`TelegramAlerter` sends messages to a configured chat ID via the Telegram Bot API. Alerts are fired by:

- `HeartbeatMonitor` — when any module's heartbeat goes stale
- `RiskRegistry` — when the daily loss limit is breached
- `TickRegistry` — when the circuit breaker opens

Configuration:

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id   # or group chat ID (negative number)
```

Leave both empty to disable alerting without changing any other code.
