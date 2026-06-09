# api/routers

FastAPI routers. Each file is a self-contained `APIRouter` mounted in `api/app.py`.

| File | Prefix | Endpoints |
|------|--------|-----------|
| `algos.py` | `/api/algos` | List algo configs and states |
| `auth.py` | `/api/auth` | Zerodha OAuth login flow, token storage |
| `broker.py` | `/api/broker` | Place/cancel orders, get open positions |
| `charts.py` | `/api/charts` | Indicator log data for the dashboard chart view |
| `data.py` | `/api/data` | Instrument search, historical candle fetch |
| `market.py` | `/api/market` | Live tick, candle, position, and heartbeat snapshots |
| `pnl.py` | `/api/pnl` | Daily and per-trade PnL summary |
| `reports.py` | `/api/reports` | Full trade report generation |
| `stream.py` | `/api/stream` | Server-sent events (SSE) tick/candle stream to the browser |

**`_helpers.py`** — shared dependency helpers (session factory injection, pagination).

**`_middleware.py`** — request logging and error-handling middleware.
