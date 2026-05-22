# strategies/

Backtest-level tests for all built-in strategies. Each test runs the full pipeline (candle feed → strategy → risk → executor) against a Postgres-backed backtest session.

## Files

| File | What it tests |
|------|--------------|
| `test_backtest.py` | Full `BacktestSession` run; validates final equity, trade count, and report generation |
| `test_walk_forward.py` | Walk-forward analysis: in-sample fit, out-of-sample validation across rolling windows |
| `test_monte_carlo.py` | Monte Carlo equity curve simulation for strategy robustness |
| `test_hyperparam_search.py` | EMA crossover parameter grid search |
| `test_vwap_search.py` | VWAP reversion parameter grid search |
| `test_rsi_search.py` | RSI mean-reversion parameter grid search |
| `test_orb_search.py` | Opening range breakout parameter grid search |
| `test_parameter_sensitivity.py` | Sensitivity analysis: how much does equity change per parameter step? |
| `test_diagnose_signals.py` | Signal diagnosis: entry/exit timing, win rate, average hold |
| `test_stress.py` | Stress test: runs `BacktestSession` on crash, trending, and random-walk scenarios |

## How backtests work

Tests use `BacktestSession` from the `testing/` library, which:
1. Loads OHLCV candles from Postgres (or synthetic data generators in `testing/backtesting/`).
2. Replays candles through `CandleAggregator` → strategy → `RiskFilter` → `OrderExecutor`.
3. Uses `PaperBroker` for fills and `SimulatedClock` for deterministic timestamps.
4. Returns a report with equity curve, trade log, and per-strategy metrics.
