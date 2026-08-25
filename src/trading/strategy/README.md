# strategy

Strategy execution — runs strategy instances against candle data and emits `SignalEvent`s.

## Layout

```
strategy/
├── api/
│   ├── __init__.py       Re-exports: SignalEvent, SignalGenerator, AlgoInstance, AlgoRunConfig,
│   │                                 Strategy, Signal
│   ├── interfaces.py     AbstractCandleStore, AbstractChartStore, AbstractConfigStore,
│   │                     AbstractAuditStore, CacherFactory protocols
│   └── schemas.py        SignalEvent (re-export from core.schemas)
├── service/
│   └── generator.py      SignalGenerator — fan-out registry; pushes bars into PolarsStore,
│                         calls strategy.on_candle(), logs signals
├── storage/
│   ├── models.py         AlgoConfig, AlgoState, Signal, IndicatorLog, DecisionLog ORM models
│   └── store.py          ConfigStore (algo config + state upsert), ChartStore (indicator logs)
└── di/
    └── providers.py      StrategyProvider
```

`Strategy`, `Signal`, `AlgoInstance`, and `AlgoRunConfig` now live in `trading_strategy_sdk` —
`strategy/api/__init__.py` imports them directly, there's no local wrapper. The concrete strategy
implementations (`ema_crossover`, `rsi_mean_reversion`, `vwap_reversion`, `dpo_mean_reversion`,
`linreg_trend`, `opening_range_breakout`, `squeeze_breakout`) also live there but aren't imported
through `strategy/api`: they're resolved by config-driven `strategy_id` via
`trading_strategy_sdk.factory.create_strategy()`, called from `di/providers/algo_pipeline.py`.

## Key concepts

**`Strategy`** (`trading_strategy_sdk.base`) — ABC for all strategy implementations. Subclasses implement `on_candle(symbol, instrument_type, candle) -> Signal | None` and optionally `warmup(symbol, candles)`.

**`SignalGenerator`** maintains a `PolarsStore` (in-memory bar window per symbol/interval). On each `CandleEvent` it pushes the bar, calls the relevant strategy's `on_candle()`, and if a `Signal` is returned, wraps it in a `SignalEvent` and fires it downstream.

**`AlgoInstance`** holds a single `(strategy, symbol, instrument_type)` binding. `bars_seen` tracks warmup progress; `is_ready()` returns `True` once `warmup_candles` bars have been processed.

**`ConfigStore`** persists `AlgoConfig` and `AlgoState` rows. Strategy state (rolling values, warmup counts) is saved after each bar so it can be restored across restarts via `SignalGenerator.restore_state()`.

## Imports

```python
from trading.strategy.api import SignalGenerator, SignalEvent
```
