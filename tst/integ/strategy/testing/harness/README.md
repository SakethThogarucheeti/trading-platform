# harness/

Low-level test harness for driving a single strategy with a controlled candle sequence.

Useful for unit-style backtest tests where you want precise control over each candle (e.g., inject exactly 5 candles then assert a signal was generated on the 5th). Lighter than `BacktestSession` — no Postgres required, no full pipeline wiring.

## Typical use

```python
harness = StrategyHarness(strategy=EmaCrossoverStrategy(...), clock=SimulatedClock())
harness.feed(candle_sequence)
signals = harness.signals
assert len(signals) == 1
assert signals[0].side == Side.BUY
```
