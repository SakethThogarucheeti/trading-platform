# candles

Converts raw tick events into OHLCV candle bars and provides historical candle access for strategy warmup.

## Layout

```
candles/
├── api/
│   ├── __init__.py       Re-exports: CandleEvent, CandleAggregator, CandleAggregatorComponent,
│   │                                 CandleConfig, CandleDataStore, InstrumentStore, Instrument,
│   │                                 HistoricalDataService
│   ├── interfaces.py     AbstractCandleStore, AbstractHistoricalSource, AbstractAuditStore,
│   │                     AbstractCandleConsumer protocols
│   └── schemas.py        CandleEvent (re-export from core.schemas)
├── service/
│   ├── aggregator.py     CandleAggregator — tick → bar; CandleAggregatorComponent lifecycle wrapper
│   ├── bar_accumulator.py BarAccumulator — pure OHLCV math; SymbolConfig
│   ├── historical.py     HistoricalDataService — DB-first fetch with broker fallback
│   └── persister.py      CandlePersister — writes closed bars to DB; CandleConfig
├── storage/
│   ├── models.py         Candle, Instrument ORM models
│   └── store.py          CandleDataStore (candle persistence), InstrumentStore (instrument upsert/lookup)
└── di/
    └── providers.py      CandlesProvider
```

## Key concepts

**`CandleAggregator`** accumulates ticks into bars per symbol/interval. When a bar closes it emits a `CandleEvent` and fires `CandlePersister.log()` as a background task.

**`CandleAggregatorComponent`** wraps `CandleAggregator` as a lifecycle `Component`. On startup it calls `HistoricalDataService.fetch()` to pre-seed all registered strategy consumers with warmup candles before live ticks arrive.

**`BarAccumulator`** is the pure-math core — no IO, no async. It holds an in-memory bar per `(symbol, interval)` key and returns a completed `CandleEvent` when the bar boundary is crossed.

**`HistoricalDataService`** fetches candles from the DB first; falls back to the broker's `fetch_candles` API if coverage is insufficient. Results are returned as a Polars DataFrame.

**`InstrumentStore`** owns the `instruments` table — upsert on login, lookup by token for tick routing.

## Imports

```python
from trading.candles.api import CandleAggregator, CandleEvent, HistoricalDataService
```
