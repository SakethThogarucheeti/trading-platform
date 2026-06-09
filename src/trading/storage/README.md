# storage

Shared storage infrastructure. Domain-specific stores live in their owning module's `storage/` layer; this package contains only cross-cutting storage concerns.

## Layout

```
storage/
├── cache/          Two-tier cache (in-memory ValueCache + optional Redis)
└── stores/
    └── candle_store.py   CandleStore — Postgres + Redis-cached AbstractCandleStore
                          for the indicator library (quantindicators)
```

## cache/

See [cache/README.md](cache/README.md).

## stores/candle_store.py

`CandleStore` implements `quantindicators.store.AbstractCandleStore`. It wraps a `CandleDataStore` (from `trading.candles.storage.store`) with an optional Redis cache layer. Indicator objects fetch candle windows through this store during `on_candle()` callbacks.

```python
from trading.storage.stores.candle_store import CandleStore
```

Cache key format: `cs:candles:{symbol}:{interval}:n{limit}` or `cs:candles:{symbol}:{interval}:since:{iso}`. TTL: 90 seconds.

## What moved out

All domain store classes previously in `storage/stores/` have been migrated to their owning modules:

| Store | Now lives in |
|-------|-------------|
| `AuditStore` | `trading.tick_ingest.storage.store` |
| `CandleDataStore` | `trading.candles.storage.store` |
| `InstrumentStore` | `trading.candles.storage.store` |
| `TradingStore` | `trading.execution.storage.store` |
| `PositionStore` | `trading.execution.storage.store` |
| `HeartbeatStore` | `trading.monitoring.storage.store` |
| `ChartStore`, `ConfigStore` | `trading.strategy.storage.store` |
