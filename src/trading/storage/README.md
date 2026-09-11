# storage

Shared storage infrastructure. Domain-specific stores live in their owning module's `storage/` layer; this package contains only cross-cutting storage concerns.

## Layout

```
storage/
└── cache/          In-memory ValueCache
```

## cache/

See [cache/README.md](cache/README.md).

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
