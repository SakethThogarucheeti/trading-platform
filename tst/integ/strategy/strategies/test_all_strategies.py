"""
Backtest all registered strategies against synthetic market scenarios.

Each strategy is run against a trending market and a random-walk market.
The test verifies that every strategy completes without errors and produces
valid metrics — it is not a performance gate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from testing.backtesting.engine import BacktestSession
from testing.backtesting.report import BacktestConfig
from testing.utils.generators import random_walk_ohlcv, trending_market

from trading.config.settings import AlgoSettings
from trading.strategy.factory import registered_strategies

_ALL_STRATEGIES = list(registered_strategies().keys())

_START = datetime(2024, 1, 2, 9, 15, 0, tzinfo=UTC)
_END = datetime(2024, 6, 30, 18, 0, 0, tzinfo=UTC)
_N_BARS = 1000
_EQUITY = 100_000.0


def _algo(strategy_id: str) -> AlgoSettings:
    return AlgoSettings(
        name=f"bt_{strategy_id}",
        instruments=["INFY"],
        strategy_id=strategy_id,
        candle_intervals=["1min"],
        equity=_EQUITY,
    )


class _InMemoryLoader:
    def __init__(self, df):
        self._df = df

    def load(self, symbol, interval, start, end):
        import polars as pl
        return self._df.filter((pl.col("date") >= start) & (pl.col("date") <= end))


@pytest.mark.parametrize("strategy_id", _ALL_STRATEGIES)
async def test_strategy_trending_market(strategy_id, pg_engine, tmp_path):
    """Every strategy must complete on a trending market and return valid metrics."""
    df = trending_market(n_bars=_N_BARS, drift=0.0004, start_price=1500.0, seed=42, start=_START)
    config = BacktestConfig(
        algo=_algo(strategy_id),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=_EQUITY,
        slippage_pct=0.05,
    )
    session = BacktestSession(config=config, db_engine=pg_engine, results_dir=tmp_path)
    report = await session.run()

    assert report is not None
    assert report.total_trades >= 0
    assert 0.0 <= report.win_rate <= 1.0
    assert report.profit_factor >= 0.0

    print(
        f"\n  {strategy_id:30}  [trending]  "
        f"trades={report.total_trades:3}  "
        f"win={report.win_rate:.0%}  "
        f"dd={report.max_drawdown:.1%}  "
        f"pnl={report.final_equity - _EQUITY:+,.0f}"
    )


@pytest.mark.parametrize("strategy_id", _ALL_STRATEGIES)
async def test_strategy_random_walk(strategy_id, pg_engine, tmp_path):
    """Every strategy must complete on a random walk and return valid metrics."""
    df = random_walk_ohlcv(n_bars=_N_BARS, start_price=1500.0, seed=7, start=_START)
    config = BacktestConfig(
        algo=_algo(strategy_id),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=_EQUITY,
        slippage_pct=0.05,
    )
    session = BacktestSession(config=config, db_engine=pg_engine, results_dir=tmp_path)
    report = await session.run()

    assert report is not None
    assert report.total_trades >= 0
    assert 0.0 <= report.win_rate <= 1.0
    assert report.profit_factor >= 0.0

    print(
        f"\n  {strategy_id:30}  [random]    "
        f"trades={report.total_trades:3}  "
        f"win={report.win_rate:.0%}  "
        f"dd={report.max_drawdown:.1%}  "
        f"pnl={report.final_equity - _EQUITY:+,.0f}"
    )
