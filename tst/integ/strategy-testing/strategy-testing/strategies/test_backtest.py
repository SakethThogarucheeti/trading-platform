"""
BacktestSession integration tests.

Uses real Postgres (via testcontainers) so DB transactions, idempotency
checks, and position tracking run against the actual engine — no SQLite shims.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from testing.backtesting.data_loader import DataLoader, FileDataLoader
from testing.backtesting.engine import BacktestSession
from testing.backtesting.report import BacktestConfig
from testing.utils.generators import crash_scenario, random_walk_ohlcv, trending_market

from trading.config.settings import AlgoSettings

# Directory written by `uv run fetch-data` — skip real-data tests when absent
_DATA_DIR = Path(__file__).parents[5] / "data"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _InMemoryLoader(DataLoader):
    """DataLoader backed by a pre-built DataFrame — no file I/O."""

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df

    def load(self, symbol: str, interval: str, start: datetime, end: datetime) -> pl.DataFrame:
        return self._df.filter((pl.col("date") >= start) & (pl.col("date") <= end))


_START = datetime(2024, 1, 2, 9, 15, 0, tzinfo=UTC)
_END = datetime(2024, 1, 2, 18, 0, 0, tzinfo=UTC)


def _algo(name: str = "test") -> AlgoSettings:
    return AlgoSettings(
        name=name,
        instruments=["INFY"],
        strategy_id="ema_crossover",
        candle_intervals=["1min"],
    )


async def _run(config, pg_engine, tmp_path):
    session = BacktestSession(
        config=config,
        db_engine=pg_engine,
        results_dir=tmp_path,
    )
    return await session.run()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_backtest_completes_without_error(pg_engine, tmp_path):
    """A basic backtest run must complete and return a BacktestReport."""
    df = trending_market(n_bars=500, drift=0.0003, start_price=1500.0, seed=42, start=_START)
    config = BacktestConfig(
        algo=_algo(),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=100_000.0,
        slippage_pct=0.05,
    )
    report = await _run(config, pg_engine, tmp_path)

    assert report is not None
    assert report.session_id != ""
    assert report.session_type == "backtest"
    assert report.started_at <= report.finished_at


async def test_backtest_equity_curve_starts_at_initial_equity(pg_engine, tmp_path):
    """The first row of the equity curve must equal initial_equity."""
    df = trending_market(n_bars=500, drift=0.0002, seed=1, start=_START)
    config = BacktestConfig(
        algo=_algo("eq_start"),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=50_000.0,
        slippage_pct=0.0,
    )
    report = await _run(config, pg_engine, tmp_path)

    assert float(report.equity_curve["equity"][0]) == pytest.approx(50_000.0)


async def test_backtest_metrics_in_valid_range(pg_engine, tmp_path):
    """Metric values must be in their expected ranges regardless of market data."""
    df = random_walk_ohlcv(n_bars=500, seed=7, start=_START)
    config = BacktestConfig(
        algo=_algo("metrics"),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=100_000.0,
        slippage_pct=0.05,
    )
    report = await _run(config, pg_engine, tmp_path)

    assert 0.0 <= report.max_drawdown <= 1.0
    assert 0.0 <= report.win_rate <= 1.0
    assert report.profit_factor >= 0.0
    assert report.total_trades >= 0


async def test_backtest_crash_scenario_produces_drawdown(pg_engine, tmp_path):
    """A 30% crash mid-session should produce valid metrics regardless of trade count."""
    df = crash_scenario(n_bars=500, crash_bar=250, crash_pct=0.30, seed=0, start=_START)
    config = BacktestConfig(
        algo=_algo("crash"),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=100_000.0,
        slippage_pct=0.05,
    )
    report = await _run(config, pg_engine, tmp_path)

    assert report.max_drawdown >= 0.0
    assert report.final_equity > 0.0


async def test_backtest_trending_market_generates_signals(pg_engine, tmp_path):
    """A strong uptrend should trigger at least one EMA crossover signal."""
    df = trending_market(n_bars=500, drift=0.001, start_price=1500.0, seed=10, start=_START)
    config = BacktestConfig(
        algo=_algo("trending"),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=100_000.0,
        slippage_pct=0.05,
    )
    report = await _run(config, pg_engine, tmp_path)

    assert report.total_trades >= 1, (
        "Trending market over 500 bars should generate at least one EMA crossover trade"
    )


async def test_backtest_html_report_generated(pg_engine, tmp_path):
    """BacktestReport.to_html() must return a non-empty HTML string with Plotly."""
    df = trending_market(n_bars=300, drift=0.0003, seed=3, start=_START)
    config = BacktestConfig(
        algo=_algo("html"),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=100_000.0,
    )
    report = await _run(config, pg_engine, tmp_path)

    html = report.to_html()
    assert "<html" in html.lower()
    assert "plotly" in html.lower()


async def test_backtest_session_report_persisted(pg_engine, tmp_path):
    """After run(), the report JSON and HTML must be written to results_dir."""
    df = trending_market(n_bars=300, drift=0.0003, seed=5, start=_START)
    config = BacktestConfig(
        algo=_algo("persist"),
        start=_START,
        end=_END,
        loader=_InMemoryLoader(df),
        initial_equity=100_000.0,
    )
    report = await _run(config, pg_engine, tmp_path)

    session_dir = tmp_path / report.session_id
    assert (session_dir / "report.json").exists(), "report.json must be written"
    assert (session_dir / "report.html").exists(), "report.html must be written"


# ---------------------------------------------------------------------------
# Real market data — EMA crossover on fetched Zerodha data
# Skipped automatically when data/ directory is absent (CI without credentials)
# ---------------------------------------------------------------------------

_REAL_DATA_SYMBOLS = ["INFY", "TCS", "RELIANCE", "HDFCBANK", "ICICIBANK"]
_REAL_START = datetime(2025, 6, 1, tzinfo=UTC)
_REAL_END = datetime(2026, 4, 17, tzinfo=UTC)
_REAL_EQUITY = 10_000.0


def _real_algo(name: str, symbols: list[str]) -> AlgoSettings:
    return AlgoSettings(
        name=name,
        instruments=symbols,
        strategy_id="ema_crossover",
        candle_intervals=["15min"],
        equity=_REAL_EQUITY,
    )


@pytest.mark.skipif(
    not _DATA_DIR.exists(), reason="data/ directory not found — run uv run fetch-data first"
)
async def test_ema_crossover_real_data_completes(pg_engine, tmp_path):
    """EMA crossover on real Zerodha data must complete and produce a valid report."""
    config = BacktestConfig(
        algo=_real_algo("ema_real", _REAL_DATA_SYMBOLS),
        start=_REAL_START,
        end=_REAL_END,
        loader=FileDataLoader(_DATA_DIR),
        initial_equity=_REAL_EQUITY,
        slippage_pct=0.05,
    )
    report = await _run(config, pg_engine, tmp_path)

    assert report is not None
    assert report.total_trades >= 0
    assert report.final_equity > 0
    assert 0.0 <= report.max_drawdown <= 1.0
    assert 0.0 <= report.win_rate <= 1.0

    print(f"\n{'=' * 55}")
    print("  EMA Crossover — Real Data Backtest")
    print(f"  Symbols  : {', '.join(_REAL_DATA_SYMBOLS)}")
    print(f"  Period   : {_REAL_START.date()} to {_REAL_END.date()}")
    print(f"  Equity   : {_REAL_EQUITY:,.0f} -> {report.final_equity:,.2f}")
    print(f"  Trades   : {report.total_trades}")
    print(f"  Win rate : {report.win_rate:.1%}")
    print(f"  Max DD   : {report.max_drawdown:.1%}")
    print(f"  Sharpe   : {report.sharpe_ratio:.2f}")
    print(f"  CAGR     : {report.cagr:.1%}")
    print(f"  Report   : {tmp_path / report.session_id / 'report.html'}")
    print(f"{'=' * 55}")


@pytest.mark.skipif(
    not _DATA_DIR.exists(), reason="data/ directory not found — run uv run fetch-data first"
)
@pytest.mark.parametrize("symbol", _REAL_DATA_SYMBOLS)
async def test_ema_crossover_per_symbol(pg_engine, tmp_path, symbol):
    """EMA crossover on each symbol individually — surfaces per-symbol edge cases."""
    config = BacktestConfig(
        algo=_real_algo(f"ema_{symbol.lower()}", [symbol]),
        start=_REAL_START,
        end=_REAL_END,
        loader=FileDataLoader(_DATA_DIR),
        initial_equity=_REAL_EQUITY,
        slippage_pct=0.05,
    )
    report = await _run(config, pg_engine, tmp_path)

    assert report.final_equity > 0
    print(
        f"\n  {symbol:12} trades={report.total_trades:3}  "
        f"win={report.win_rate:.0%}  dd={report.max_drawdown:.1%}  "
        f"final={report.final_equity:,.0f}"
    )
