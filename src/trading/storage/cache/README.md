# storage/cache/

Two-tier caching layer (in-memory + Redis via cashews). Keeps hot values — daily P&L, rolling strategy state, dashboard API responses — out of the database on the hot path.

## Architecture

```
CacherFactory          (single DI-injected singleton)
    │
    ├── PnlCacher          domain: daily realized P&L
    ├── RollingStateCacher domain: per-tick strategy rolling state
    └── ApiResponseCacher  domain: dashboard API response bodies
          │
          └── BaseCacher[T]  (abstract base — get_or_set, make_key, default_ttl)
                │
                └── ValueCache  (backend: in-memory dict + cashews Redis)
```

## Files

| File | Class | Purpose |
|------|-------|---------|
| `backend.py` | `ValueCache` | Two-tier backend: in-memory dict (sync-safe, always current) + Redis via cashews (async, survives restarts). Memory is always checked first; Redis is only consulted on a miss and the result is written back into memory. |
| `base.py` | `BaseCacher[T]` | Abstract base. Subclasses define `make_key(*args)` and `default_ttl()`. Provides `get_or_set(key_args, producer, ttl)` — calls the producer only on a cache miss. |
| `pnl.py` | `PnlCacher` | Daily realized P&L. Written by `OrderExecutor` on every fill (`increment_sync`); read by `RiskFilter` before each signal check. Key: `rf:pnl:{YYYY-MM-DD}`. TTL: seconds until midnight + 1h grace. |
| `rolling_state.py` | `RollingStateCacher` | Per-tick rolling strategy state (previous indicator values, bar counters). Written by `SignalGenerator` after every `on_candle()`; read on startup to restore state across restarts. Keeps a sliding window of the last 50 `tick_log_id`s per `(algo, symbol, interval)`. |
| `api.py` | `ApiResponseCacher` | TTL-based cache for dashboard API response bodies (already-serialized JSON strings). Short TTLs (30–60s) keep the dashboard fast without hammering the DB on every poll. |
| `factory.py` | `CacherFactory` | Lazily creates and reuses all typed cachers. DI injects this as an app-scoped singleton — callers use `factory.pnl()`, `factory.rolling_state()`, `factory.api()`. |

## Usage pattern

```python
# Cache-aside with producer callback
value = await cacher.get_or_set(
    key_args=(today,),
    producer=lambda: store.get_daily_realized_pnl(today),
    ttl=60,
)
```

The producer is an async callable invoked only on a cache miss. The result is written to both memory and Redis before being returned.

## Relationship to other packages

- `storage/cache` is injected via `di/providers/` as a `CacherFactory` singleton
- `execution/order_executor.py` — calls `PnlCacher.increment_sync()` on fill
- `risk/` — reads `PnlCacher` via `RiskContext` before each signal check
- `strategy/` — reads/writes `RollingStateCacher` for state continuity across restarts
- `api/routers/pnl.py`, `api/routers/reports.py` — use `ApiResponseCacher` to short-circuit repeated dashboard polls
