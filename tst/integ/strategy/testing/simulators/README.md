# simulators/

Synthetic OHLCV price generators for backtest testing without real market data.

| Generator | Behavior |
|-----------|---------|
| `random_walk_ohlcv()` | Brownian motion — no trend, stationary |
| `trending_market()` | Controlled uptrend or downtrend with configurable drift |
| `crash_scenario()` | Sharp drawdown (configurable depth and speed) followed by recovery |

Each generator produces a list of `CandleEvent` objects that can be fed directly into `BacktestSession` or the `StrategyHarness`.
