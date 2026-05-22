# storage/stores/

Domain-specific store implementations. Each store owns one slice of the DB schema.

## Files

| File | Key class | Responsibility |
|------|-----------|---------------|
| `audit.py` | `AuditStore` | Tick logs + per-decision audit trail |
| `candle.py` | `CandleDataStore` | OHLCV candle persistence and retrieval |
| `candle_store.py` | `CandleStore` | Candle fetch with optional Redis cache (for indicator computation) |
| `chart.py` | `ChartStore` | Indicator value logging for charting and backtests |
| `config.py` | `ConfigStore` | Algo config rows + live algo state |
| `heartbeat.py` | `HeartbeatStore` | Module liveness timestamps |
| `instrument.py` | `InstrumentStore` | Instrument master (symbol, token, type) |
| `trading.py` | `TradingStore` | Signals, orders, positions, broker tokens |

## Store overview

**`AuditStore`** — every tick that enters the system gets a `TickLog` row (returns the DB-assigned `tick_log_id` that propagates downstream). Every algo decision step (signal generated, order placed, fill received) gets a `DecisionLog` row with full JSON context.

**`CandleDataStore`** — persists OHLCV bars. `save_candles()` uses `ON CONFLICT DO NOTHING` so warmup and live candles are idempotent. Used by `CandleAggregator` after each bar closes.

**`CandleStore`** — wraps `CandleDataStore` with optional Redis caching keyed by `(symbol, interval, limit/since)`. Shared across all indicators querying the same window; TTL is 90 seconds.

**`TradingStore`** — the widest store. Manages the full order lifecycle (`save_order` → `update_order_status` → position tracking), signal persistence, and encrypted broker token storage.

**`ConfigStore`** — `seed_algo_config()` writes the algo's strategy params to DB on startup; `upsert_algo_state()` snapshots live indicator state periodically for recovery after restarts.

## Abstract interfaces

Each store exports an `Abstract*` base class (e.g., `AbstractTradingStore`). These are used as the DI injection type and as the parameter type in components, making it easy to swap in test doubles.

The abstract classes are provided by `di/providers/infra.py` and injected via Dishka.
