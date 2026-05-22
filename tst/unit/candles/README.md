# tst/unit/candles/

Unit tests for `src/trading/candles/`.

## Files

| File | What it tests |
|------|--------------|
| `test_bar_accumulator.py` | `BarAccumulator` state machine: first tick handling, intra-bar OHLCV accumulation, bar boundary detection, multi-interval tracking |
| `test_candle_aggregator.py` | `CandleAggregatorComponent` lifecycle, warmup candle replay, algo registry callback registration, error handling on bad tick |
