"""Quick RSI debug test — 1 week of real data to surface errors fast."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from testing.backtesting.engine import BacktestSession
from testing.backtesting.report import BacktestConfig
from testing.backtesting.data_loader import FileDataLoader

from trading.config.settings import AlgoSettings

_DATA_DIR = Path(__file__).parents[4] / "data"
_SYMBOLS = ["INFY", "TCS", "RELIANCE", "HDFCBANK", "ICICIBANK"]
_START = datetime(2025, 6, 1, tzinfo=UTC)
_END = datetime(2026, 5, 25, tzinfo=UTC)
_EQUITY = 100_000.0


@pytest.mark.skipif(not _DATA_DIR.exists(), reason="data/ directory not found")
async def test_rsi_quick(pg_engine, tmp_path):
    config = BacktestConfig(
        algo=AlgoSettings(
            name="rsi_quick",
            instruments=_SYMBOLS,
            strategy_id="rsi_mean_reversion",
            candle_intervals=["15min"],
            equity=_EQUITY,
        ),
        start=_START,
        end=_END,
        loader=FileDataLoader(_DATA_DIR),
        initial_equity=_EQUITY,
        slippage_pct=0.05,
    )
    session = BacktestSession(config=config, db_engine=pg_engine, results_dir=tmp_path)
    report = await session.run()

    pnl = report.final_equity - _EQUITY
    print(f"\n{'=' * 65}")
    print(f"  rsi_mean_reversion (1-week quick)")
    print(f"  Period   : {_START.date()} to {_END.date()}")
    print(f"  Equity   : {_EQUITY:,.0f} -> {report.final_equity:,.2f}  ({pnl:+,.0f})")
    print(f"  Trades   : {report.total_trades}")
    print(f"  Win rate : {report.win_rate:.1%}")
    print(f"  Max DD   : {report.max_drawdown:.1%}")
    print(f"{'=' * 65}")
