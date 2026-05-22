# candles/

Converts raw `TickEvent` objects into OHLCV `CandleEvent` objects. Add new bar-building logic here.

## Files

| File | Purpose |
|------|---------|
| `bar_accumulator.py` | Pure in-memory OHLCV state machine — no I/O |
| `candle_aggregator.py` | Warmup + live aggregation + registry dispatch |

## How it works

`BarAccumulator` is the pure core: given a tick for a `(symbol, interval)` pair, it either updates the current partial bar or closes the completed bar and opens a new one. Intervals are aligned to wall-clock boundaries (e.g., a 5-minute bar opens at 09:15:00, not 09:16:34).

`CandleAggregator` wraps the accumulator with:
1. **Warmup** — fetches historical OHLCV from the broker before live ticks arrive and replays them through registered algo registries so strategies start with warm indicator buffers.
2. **Live aggregation** — feeds live ticks through `BarAccumulator` and emits a `CandleEvent` each time a bar closes.
3. **Persistence** — logs each emitted candle to `CandleDataStore` and audits via `AuditStore`.
4. **Registry dispatch** — calls every registered algo's `on_candle` callback with the completed `CandleEvent`.

`CandleAggregatorComponent` wraps `CandleAggregator` as a `Component` for integration with the `Runtime` lifecycle.

## Key classes

- `BarAccumulator` — stateful dict of `(symbol, interval) → PartialBar`; returns `CandleEvent | None` on each tick
- `CandleAggregator` — owns `BarAccumulator`, drives warmup, and dispatches to registries
- `CandleAggregatorComponent` — `Component` subclass; started by `Runtime`
- `CandleConfig` — dataclass: list of `(symbol, interval)` pairs to track
