# tst/unit/tick_ingest/

Unit tests for `src/trading/tick_ingest/`.

## Files

| File | What it tests |
|------|--------------|
| `test_kite_ingestor.py` | `KiteIngestor` lifecycle, `MockBrokerStream` callback wiring, tick callback firing on validated ticks, subscription to instrument tokens |
| `test_tick_pubsub.py` | `TickPublisher` Redis publish + circuit state SET; pub/sub round-trip with `fakeredis` |
| `test_candle_aggregator.py` | Integration of `TickIngestor` → `CandleAggregator`: validated tick feeds bar accumulator and emits candle when bar closes |

Uses `fakeredis` for Redis-dependent tests; no real broker connections.
