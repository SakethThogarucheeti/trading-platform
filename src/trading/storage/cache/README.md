# storage/cache

In-memory cache used by the risk and strategy layers for values that are expensive to recompute on every tick. Single process, no external cache backend — values don't survive a process restart.

## Files

**`base.py`** — `AbstractCache[K, V]` protocol. A cache is a key-value store with async `get`, `set`, and `invalidate`.

**`backend.py`** — `ValueCache` — in-memory dict with per-key TTL.

**`factory.py`** — `CacherFactory` — the single injectable object. Vends named cache instances:
- `factory.rolling_state()` — per-algo rolling state (indicator warm-up data)

Daily realized PnL is no longer cached here — a process-local cache silently diverged across
concurrent worker processes (see `execution/storage/store.py`'s `TradingStore`, which now owns
`increment_pnl_aggregate`/`get_pnl_aggregate` backed by a `strategy_aggregates` Postgres table
updated via `SELECT ... FOR UPDATE`).

**`rolling_state.py`** — `RollingStateCache` — saves/loads per-algo-symbol-interval state snapshots (JSON-encoded), backed by `ValueCache`. Used by `SignalGenerator`; does not survive a process restart.

**`api.py`** — `ApiResponseCacher` — caches HTTP API responses (e.g. instrument list), backed by `ValueCache`.

## Setup

```python
from trading.storage.cache import CacherFactory, ValueCache

factory = CacherFactory(ValueCache(), clock)
```
