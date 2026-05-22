"""
Parameter sensitivity tests.

Verifies that the metric functions and data generators behave correctly
under varying input conditions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from testing.backtesting.metrics import (
    cagr,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from testing.backtesting.portfolio import TradeRecord
from testing.utils.generators import random_walk_ohlcv, trending_market

# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------


def _equity_curve(values: list[float]) -> pl.DataFrame:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    dates = [start + timedelta(days=i) for i in range(len(values))]
    return pl.DataFrame({"date": dates, "equity": values})


def _trade(pnl: float) -> TradeRecord:
    now = datetime.now(UTC)
    return TradeRecord(
        symbol="X",
        side="BUY",
        qty=1,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        entry_time=now,
        exit_time=now,
    )


def test_sharpe_positive_for_uptrend():
    """Steadily growing equity curve must have positive Sharpe ratio."""
    curve = _equity_curve([100_000 + i * 100 for i in range(252)])
    sr = sharpe_ratio(curve)
    assert sr > 0, f"Expected positive Sharpe for uptrend, got {sr}"


def test_sharpe_zero_for_flat_curve():
    """Flat equity curve (no returns) must have Sharpe of 0."""
    curve = _equity_curve([100_000.0] * 50)
    sr = sharpe_ratio(curve)
    assert sr == pytest.approx(0.0)


def test_max_drawdown_zero_for_monotone_growth():
    """Monotonically growing equity must have zero max drawdown."""
    curve = _equity_curve([100_000 + i * 1000 for i in range(100)])
    mdd = max_drawdown(curve)
    assert mdd == pytest.approx(0.0)


def test_max_drawdown_detects_peak_to_trough():
    """Drawdown from 100k → 80k is 20%."""
    values = [100_000, 110_000, 100_000, 80_000, 90_000]
    curve = _equity_curve(values)
    mdd = max_drawdown(curve)
    # Peak is 110k, trough is 80k → (110k - 80k) / 110k ≈ 0.272
    assert mdd == pytest.approx((110_000 - 80_000) / 110_000, abs=1e-4)


def test_win_rate_correct():
    trades = [_trade(100), _trade(-50), _trade(200), _trade(-30), _trade(10)]
    wr = win_rate(trades)
    assert wr == pytest.approx(3 / 5)


def test_profit_factor_infinite_for_no_losses():
    trades = [_trade(100), _trade(200)]
    pf = profit_factor(trades)
    assert pf == float("inf")


def test_profit_factor_zero_for_no_wins():
    trades = [_trade(-100), _trade(-50)]
    pf = profit_factor(trades)
    assert pf == 0.0


def test_cagr_positive_for_growing_equity():
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2022, 1, 1, tzinfo=UTC)  # ~2 years
    curve = pl.DataFrame(
        {
            "date": [start, end],
            "equity": [100_000.0, 121_000.0],  # 10% annual growth → 21% over 2yr
        }
    )
    c = cagr(curve, 100_000.0)
    assert c == pytest.approx(0.10, abs=0.005)


# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------


def test_random_walk_produces_correct_columns():
    df = random_walk_ohlcv(n_bars=50)
    assert set(df.columns) == {"date", "open", "high", "low", "close", "volume"}
    assert len(df) == 50


def test_all_prices_positive():
    df = random_walk_ohlcv(n_bars=200, seed=0)
    assert (df["close"] > 0).all()
    assert (df["open"] > 0).all()
    assert (df["high"] > 0).all()
    assert (df["low"] > 0).all()


def test_high_gte_close_and_open():
    df = random_walk_ohlcv(n_bars=200, seed=1)
    assert (df["high"] >= df["close"]).all()
    assert (df["high"] >= df["open"]).all()


def test_low_lte_close_and_open():
    df = random_walk_ohlcv(n_bars=200, seed=2)
    assert (df["low"] <= df["close"]).all()
    assert (df["low"] <= df["open"]).all()


def test_trending_market_ends_higher():
    """With positive drift the last price should be above the start on average."""
    import statistics

    finals = []
    for seed in range(20):
        df = trending_market(n_bars=500, drift=0.001, seed=seed)
        finals.append(float(df["close"][-1]))
    assert statistics.mean(finals) > 1000.0, "Trending market should end higher on average"
