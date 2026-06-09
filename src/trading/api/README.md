# api

FastAPI HTTP layer — serves the trading dashboard and provides REST endpoints for the frontend.

## Files

**`app.py`** — FastAPI application factory. Mounts all routers and wires the Dishka DI container into the app lifespan.

**`server.py`** — `ApiServer` Component. Wraps the FastAPI app as a lifecycle `Component` so it starts/stops cleanly with the rest of the server process.

**`telegram.py`** — `TelegramAlerter`. Implements `AbstractAlerter` from `monitoring.api.interfaces`. Sends alerts to a configured Telegram bot/chat when stale heartbeats are detected.

## Routers

See [routers/README.md](routers/README.md).
