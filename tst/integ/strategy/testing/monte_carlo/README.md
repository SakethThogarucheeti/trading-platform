# monte_carlo/

Monte Carlo simulation engine for strategy robustness testing.

Takes a completed backtest's trade log and generates N simulated equity curves by bootstrapping trade returns in random order. Used to answer: "how likely is this drawdown to be noise vs a structural flaw?"

## Key outputs

- Distribution of final equity across N simulations
- 5th/95th percentile equity curves
- Probability of ruin (equity dropping below a threshold)
- Sharpe ratio distribution

Used by `tst/integ/strategy/strategies/test_monte_carlo.py`.
