# api

FastAPI HTTP layer — serves the trading dashboard and provides REST endpoints for the frontend.

## Files

**`app.py`** — FastAPI application factory (`build_app()`). Mounts all routers. It doesn't wire a DI container itself — `ApiServer` (`server.py`), built by `ComponentContainer`, resolves the dependencies (session factory, `KiteClient`, `OrderExecutor`, etc.) and passes them in as plain constructor arguments.

**`server.py`** — `ApiServer` Component. Wraps the FastAPI app as a lifecycle `Component` so it starts/stops cleanly with the rest of the server process.

**`telegram.py`** — `TelegramAlerter`. Sends alerts to a configured Telegram bot/chat when stale heartbeats are detected.

## Routers

See [routers/README.md](routers/README.md).
