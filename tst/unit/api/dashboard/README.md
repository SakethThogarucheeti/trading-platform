# tst/unit/api/dashboard/

Unit tests for `src/trading/api/dashboard/`.

## Files

| File | What it tests |
|------|--------------|
| `test_app.py` | Core endpoints (`/api/ping`, `/api/positions`, `/api/health`, `/api/signals`, `/api/candles`, `/api/ticks`, `/api/pnl`, `/api/algos`, `/api/settings`, `/api/charts`) with mocked session factory; verifies HTTP status, response shape, and filtering by `session_id` / `algo_name`. Also covers `DashboardServer` component lifecycle (setup, teardown). |
| `test_app_reports.py` | `/api/reports/*` endpoints, live report generation with day/week/month periods, and auth endpoints (`/api/auth/login-url`, `/api/auth/callback`). |

Tests call `build_app()` directly with a mocked `session_factory` and use `httpx.AsyncClient` with `ASGITransport` for in-process HTTP testing (no real server started).
