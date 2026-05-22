# testing/

Shared backtesting harness and utilities. Importable as `import testing` from within the `tst/integ/strategy/` test suite (the `conftest.py` adds this directory to `sys.path`).

## Sub-packages

| Directory | Purpose |
|-----------|---------|
| `backtesting/` | `BacktestSession` engine + synthetic data generators |
| `harness/` | Low-level test harness for driving a single strategy with candles |
| `indicators/` | Indicator testing harness (fetch + compute + assert) |
| `monte_carlo/` | Monte Carlo simulation engine |
| `simulators/` | Price simulators: random walk, trending market, crash scenario |
| `utils/` | Shared helpers: date ranges, data loading, formatting |
| `walk_forward/` | Walk-forward split generator + aggregator |

## Design

The library is a thin wrapper around the production `trading/` code — it uses the same `CandleAggregator`, `RiskFilter`, `OrderExecutor`, and `PaperBroker` that run in production, with `SimulatedClock` injected for deterministic timestamps. This means a bug that passes backtesting will behave identically in live trading, and a bug found in live trading can be reproduced in a backtest.
