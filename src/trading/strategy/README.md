# strategy

Strategy execution — runs strategy instances against candle data and emits `SignalEvent`s.

## Layout

```
strategy/
├── api/
│   ├── __init__.py       Re-exports: SignalEvent, SignalGenerator, AlgoInstance, AlgoRunConfig,
│   │                                 Strategy, Signal, AlgoConfig, AlgoState
│   ├── interfaces.py     AbstractCandleStore, AbstractChartStore, AbstractConfigStore,
│   │                     AbstractAuditStore, CacherFactory protocols
│   └── schemas.py        SignalEvent (re-export from core.schemas)
├── service/
│   ├── base.py           AlgoInstance, AlgoRunConfig, Strategy ABC wrapper
│   └── generator.py      SignalGenerator — fan-out registry; pushes bars into PolarsStore,
│                         calls strategy.on_candle(), logs signals
├── storage/
│   ├── models.py         AlgoConfig, AlgoState, Signal, IndicatorLog, DecisionLog ORM models
│   └── store.py          ConfigStore (algo config + state upsert), ChartStore (indicator logs)
├── di/
│   └── providers.py      StrategyProvider
├── base.py               Strategy ABC (public; subclassed by all strategy impls)
├── factory.py            StrategyFactory — maps strategy_id → Strategy instance
└── <strategy files>      ema_crossover.py, rsi_mean_reversion.py, vwap_reversion.py,
                          dpo_mean_reversion.py, linreg_trend.py, opening_range_breakout.py,
                          squeeze_breakout.py
```

## Key concepts

**`Strategy`** (base.py) — ABC for all strategy implementations. Subclasses implement `on_candle(symbol, instrument_type, candle) -> Signal | None` and optionally `warmup(symbol, candles)`.

**`SignalGenerator`** maintains a `PolarsStore` (in-memory bar window per symbol/interval). On each `CandleEvent` it pushes the bar, calls the relevant strategy's `on_candle()`, and if a `Signal` is returned, wraps it in a `SignalEvent` and fires it downstream.

**`AlgoInstance`** holds a single `(strategy, symbol, instrument_type)` binding. `bars_seen` tracks warmup progress; `is_ready()` returns `True` once `warmup_candles` bars have been processed.

**`ConfigStore`** persists `AlgoConfig` and `AlgoState` rows. Strategy state (rolling values, warmup counts) is saved after each bar so it can be restored across restarts via `SignalGenerator.restore_state()`.

## Imports

```python
from trading.strategy.api import SignalGenerator, SignalEvent
```
