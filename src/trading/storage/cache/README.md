# storage/cache

Two-tier cache used by the risk and strategy layers for values that are expensive to recompute on every tick.

## Files

**`base.py`** — `AbstractCache[K, V]` protocol. A cache is a key-value store with async `get`, `set`, and `invalidate`.

**`backend.py`** — `ValueCache` — in-memory dict. Used in tests and when Redis is not configured.

**`factory.py`** — `CacherFactory` — the single injectable object. Vends named cache instances:
- `factory.pnl()` — daily PnL cache; keyed by `(date,)`
- `factory.rolling_state()` — per-algo rolling state (indicator warm-up data)

**`pnl.py`** — `PnLCache` — wraps `AbstractCache` with `increment_sync()` for fill-driven PnL updates and typed `get_or_set()`.

**`rolling_state.py`** — `RollingStateCache` — saves/loads per-algo-symbol-interval state snapshots to Redis (JSON-encoded). Used by `SignalGenerator` to survive restarts without re-warming.

**`api.py`** — `ApiResponseCacher` — caches HTTP API responses (e.g. instrument list) in Redis.

## Setup

```python
from trading.storage.cache import setup_cache, CacherFactory, ValueCache

# In production (Redis available)
setup_cache(redis_client)

# In tests
setup_cache(None)   # falls back to ValueCache
factory = CacherFactory(ValueCache(), clock)
```
