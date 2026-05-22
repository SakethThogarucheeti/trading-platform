# tst/unit/storage/

Unit tests for `src/trading/storage/`.

## Files

| File | What it tests |
|------|--------------|
| `test_repository.py` | All domain stores against an in-memory SQLite DB: `TradingStore` (signals, orders, fills, positions), `AuditStore` (tick logs, decision logs), `HeartbeatStore` (upsert + stale query), `ConfigStore` (seed + state upsert), `ChartStore` (indicator log + retrieval), `InstrumentStore` (upsert) |

The test uses `aiosqlite` as the async engine backend to avoid a real Postgres dependency in unit tests.
