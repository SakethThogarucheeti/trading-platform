"""
Stress scenario tests.

Verifies that metric functions and data generators correctly characterise
extreme market conditions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
from testing.backtesting.metrics import max_drawdown, sharpe_ratio, win_rate
from testing.backtesting.portfolio import TradeRecord
from testing.utils.generators import crash_scenario, random_walk_ohlcv, volatility_spike

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _equity_curve(values: list[float]) -> pl.DataFrame:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    dates = [start + timedelta(hours=i) for i in range(len(values))]
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


# ---------------------------------------------------------------------------
# Crash scenario tests
# ---------------------------------------------------------------------------


def test_crash_scenario_produces_large_drawdown():
    """A 30% crash should produce max drawdown > 20%."""
    df = crash_scenario(n_bars=500, crash_bar=250, crash_pct=0.30, seed=0)

    # Simulate equity curve: long from bar 0, forced out at crash
    equities = [100_000.0]
    for i, row in enumerate(df.iter_rows(named=True)):
        # Simplified: equity tracks close price
        equities.append(equities[-1] * (row["close"] / float(df["close"][max(0, i - 1)])))

    curve = _equity_curve(equities)
    mdd = max_drawdown(curve)
    assert mdd > 0.20, f"Crash scenario drawdown should exceed 20%, got {mdd:.2%}"


def test_crash_scenario_valid_ohlcv():
    """Crash scenario must produce valid OHLCV data."""
    df = crash_scenario(n_bars=300, crash_bar=150, crash_pct=0.25, seed=1)
    assert len(df) == 300
    assert (df["high"] >= df["close"]).all()
    assert (df["low"] <= df["close"]).all()
    assert (df["close"] > 0).all()


# ---------------------------------------------------------------------------
# Volatility spike tests
# ---------------------------------------------------------------------------


def test_volatility_spike_does_not_crash_generator():
    """Generator must complete without errors even at extreme multipliers."""
    df = volatility_spike(n_bars=200, spike_bar=100, spike_multiplier=10.0, seed=5)
    assert len(df) == 200
    assert (df["close"] > 0).all()


def test_volatility_spike_increases_bar_range():
    """Bars near the spike should have larger H-L range than non-spike bars."""
    df = volatility_spike(n_bars=100, spike_bar=50, spike_multiplier=5.0, seed=0)
    spike_ranges = (df["high"] - df["low"]).slice(48, 5)  # bars 48-52
    normal_ranges = (df["high"] - df["low"]).slice(0, 10)  # bars 0-9
    assert spike_ranges.mean() > normal_ranges.mean() * 1.5, (
        "Bars near spike should have larger H-L range"
    )


# ---------------------------------------------------------------------------
# Zero-volume / edge case tests
# ---------------------------------------------------------------------------


def test_metrics_handle_empty_trade_list():
    """Metric functions must return 0.0 (not crash) with empty trade list."""
    assert win_rate([]) == 0.0


def test_metrics_handle_single_bar_equity_curve():
    """Sharpe with a single bar must return 0.0 (insufficient data)."""
    curve = _equity_curve([100_000.0])
    assert sharpe_ratio(curve) == 0.0
    assert max_drawdown(curve) == 0.0


def test_random_walk_zero_volume_bars_absent():
    """Generated bars must always have non-negative volume."""
    df = random_walk_ohlcv(n_bars=1000, seed=99)
    assert (df["volume"] >= 0).all()


def test_losing_trades_negative_sharpe():
    """Steadily declining equity must produce negative Sharpe ratio."""
    values = [100_000 - i * 200 for i in range(200)]
    values = [max(v, 1.0) for v in values]  # prevent zero
    curve = _equity_curve(values)
    sr = sharpe_ratio(curve)
    assert sr < 0, f"Declining equity should have negative Sharpe, got {sr}"
