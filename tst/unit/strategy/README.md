# tst/unit/strategy/

Unit tests for `src/trading/strategy/`.

## Files

| File | What it tests |
|------|--------------|
| `test_base.py` | `Strategy` ABC contract; `EmaCrossoverStrategy` signal generation on EMA9/EMA21 crossovers |
| `test_registry.py` | Strategy lookup and registration in `StrategyFactory` |
| `test_signal_generator.py` | `SignalGenerator` pipeline: candle → strategy → signal dispatch |
| `test_strategies.py` | All built-in strategies produce signals on canonical candle sequences |
| `test_vwap_reversion.py` | `VwapReversionStrategy` specific edge cases (band entry, exit, flat in squeeze) |

All tests inject `SimulatedClock` and synthetic candle sequences — no real market data or broker calls.
