"""
Real-data backtest — all strategies on Zerodha 15min data.

Skipped automatically when data/ directory is absent.
Run `uv run fetch-data --symbols INFY TCS RELIANCE HDFCBANK ICICIBANK --intervals 15min --days 400`
to populate the data directory first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from testing.backtesting.engine import BacktestSession
from testing.backtesting.report import BacktestConfig
from testing.backtesting.data_loader import FileDataLoader

from trading.config.settings import AlgoSettings
from trading.strategy.factory import registered_strategies

_DATA_DIR = Path(__file__).parents[4] / "data"
_ALL_STRATEGIES = list(registered_strategies().keys())

_SYMBOLS = ["INFY", "TCS", "RELIANCE", "HDFCBANK", "ICICIBANK"]
_START = datetime(2026, 4, 25, tzinfo=UTC)
_END = datetime(2026, 5, 25, tzinfo=UTC)
_EQUITY = 100_000.0
_SLIPPAGE = 0.05

_skip = pytest.mark.skipif(
    not _DATA_DIR.exists(),
    reason="data/ directory not found — run fetch-data first",
)


def _algo(strategy_id: str) -> AlgoSettings:
    return AlgoSettings(
        name=f"real_{strategy_id}",
        instruments=_SYMBOLS,
        strategy_id=strategy_id,
        candle_intervals=["15min"],
        equity=_EQUITY,
    )


@_skip
@pytest.mark.parametrize("strategy_id", _ALL_STRATEGIES)
async def test_all_strategies_real_data(strategy_id, pg_engine, tmp_path):
    """Every strategy on real Zerodha 15min data — surfaces real-market behaviour."""
    config = BacktestConfig(
        algo=_algo(strategy_id),
        start=_START,
        end=_END,
        loader=FileDataLoader(_DATA_DIR),
        initial_equity=_EQUITY,
        slippage_pct=_SLIPPAGE,
    )
    session = BacktestSession(config=config, db_engine=pg_engine, results_dir=tmp_path)
    report = await session.run()

    assert report is not None
    assert report.final_equity > 0
    assert 0.0 <= report.max_drawdown <= 1.0
    assert 0.0 <= report.win_rate <= 1.0

    pnl = report.final_equity - _EQUITY
    print(f"\n{'=' * 65}")
    print(f"  {strategy_id}")
    print(f"  Symbols  : {', '.join(_SYMBOLS)}")
    print(f"  Period   : {_START.date()} to {_END.date()}")
    print(f"  Equity   : {_EQUITY:,.0f} -> {report.final_equity:,.2f}  ({pnl:+,.0f})")
    print(f"  Trades   : {report.total_trades}")
    print(f"  Win rate : {report.win_rate:.1%}")
    print(f"  Max DD   : {report.max_drawdown:.1%}")
    print(f"  Sharpe   : {report.sharpe_ratio:.2f}")
    print(f"  CAGR     : {report.cagr:.1%}")
    print(f"  Calmar   : {report.calmar_ratio:.2f}")
    print(f"  Report   : {tmp_path / report.session_id / 'report.html'}")
    print(f"{'=' * 65}")
