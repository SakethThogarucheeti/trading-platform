# tst/unit/api/dashboard/

Unit tests for `src/trading/api/dashboard/`.

## Files

| File | What it tests |
|------|--------------|
| `test_app.py` | Core endpoints (`/api/ping`, `/api/positions`, `/api/health`, `/api/signals`, `/api/candles`, `/api/ticks`, `/api/pnl`, `/api/algos`, `/api/settings`, `/api/charts`) with mocked session factory; verifies HTTP status, response shape, and filtering by `session_id` / `algo_name`. Also covers `DashboardServer` component lifecycle (setup, teardown). |
| `test_app_reports.py` | `/api/reports/*` endpoints, live report generation with day/week/month periods, and auth endpoints (`/api/auth/login-url`, `/api/auth/callback`). |
| `test_stream.py` | `/api/decisions/stream` (SSE) — the initial "connected" event, prompt termination on client disconnect, and SSE payload formatting for a polled row. Mocks `Request.is_disconnected()` and `anyio.sleep()` directly rather than relying on real timing, so the tests are deterministic and fast. |

Tests call `build_app()` directly with a mocked `session_factory` and use `httpx.AsyncClient` with `ASGITransport` for in-process HTTP testing (no real server started).
