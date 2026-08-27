# tst/unit/tick_ingest/

Unit tests for `src/trading/tick_ingest/`.

## Files

| File | What it tests |
|------|--------------|
| `test_kite_ingestor.py` | `KiteIngestor` lifecycle, `MockBrokerStream` callback wiring, tick callback firing on validated ticks (including concurrent dispatch to multiple registered callbacks), subscription to instrument tokens |
| `test_candle_aggregator.py` | Integration of `TickIngestor` → `CandleAggregator`: validated tick feeds bar accumulator and emits candle when bar closes |

No real broker connections.
