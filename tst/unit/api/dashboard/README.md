# tst/unit/api/dashboard/

Unit tests for `src/trading/api/dashboard/`.

## Files

| File | What it tests |
|------|--------------|
| `test_app.py` | All `/api/*` endpoints with mocked session factory; verifies HTTP status, response shape, and filtering by `session_id` |
| `test_app_reports.py` | `/api/reports/*` endpoints, live report generation, auth endpoints (`/api/auth/login-url`, `/api/auth/callback`) |

Tests use FastAPI's `TestClient` (sync) or `httpx.AsyncClient` with an in-memory SQLite DB (via the overridden session factory dependency).
