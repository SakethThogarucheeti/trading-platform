# backtesting/

`BacktestSession` — the main entry point for running a strategy against historical or synthetic candle data.

## Key classes

- **`BacktestSession`** — orchestrates a full backtest: loads data, warms up indicators, replays candles, collects trades, and generates a report.
- **`DataLoader`** — fetches OHLCV candles from Postgres by symbol, interval, and date range.
- Synthetic data generators (used when real data is unavailable):
  - `random_walk_ohlcv()` — Brownian motion price path
  - `trending_market()` — uptrend or downtrend with controlled drift
  - `crash_scenario()` — simulates a sharp drawdown followed by recovery

## Usage

```python
session = BacktestSession(
    engine=pg_engine,
    strategy_id="ema_crossover",
    symbol="NIFTY",
    interval="5min",
    from_date=date(2025, 1, 1),
    to_date=date(2025, 3, 31),
    params={"fast": 9, "slow": 21},
)
report = await session.run()
assert report.total_trades > 0
```
