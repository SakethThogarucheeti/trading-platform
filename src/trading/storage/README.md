# storage

Shared storage infrastructure. Domain-specific stores live in their owning module's `storage/` layer; this package contains only cross-cutting storage concerns.

## Layout

```
storage/
├── cache/          In-memory ValueCache
└── stores/
    └── candle_store.py   CandleStore — Postgres-backed AbstractCandleStore
                          for the indicator library (quantindicators)
```

## cache/

See [cache/README.md](cache/README.md).

## stores/candle_store.py

`CandleStore` implements `quantindicators.store.AbstractCandleStore`. It wraps a `CandleDataStore` (from `trading.candles.storage.store`), reading directly from Postgres. Indicator objects fetch candle windows through this store during `on_candle()` callbacks.

```python
from trading.storage.stores.candle_store import CandleStore
```

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
