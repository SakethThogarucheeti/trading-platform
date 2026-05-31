"""Unit tests for backtesting/metrics.py — pure metric functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from testing.backtesting.metrics import (
    _daily_equity,
    _daily_returns,
    cagr,
    calmar_ratio,
    max_drawdown,
    max_drawdown_duration,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from testing.backtesting.portfolio import TradeRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _curve(equities: list[float], start: datetime | None = None) -> pl.DataFrame:
    """Build a minute-bar equity curve from a list of equity values."""
    if start is None:
        start = datetime(2024, 1, 2, 9, 15, tzinfo=UTC)
    dates = [start + timedelta(minutes=i) for i in range(len(equities))]
    return pl.DataFrame({"date": dates, "equity": equities})


def _daily_curve(equities: list[float], start_date: datetime | None = None) -> pl.DataFrame:
    """Build a one-point-per-day equity curve."""
    if start_date is None:
        start_date = datetime(2024, 1, 2, 9, 15, tzinfo=UTC)
    dates = [start_date + timedelta(days=i) for i in range(len(equities))]
    return pl.DataFrame({"date": dates, "equity": equities})


def _trade(pnl: float) -> TradeRecord:
    now = datetime(2024, 1, 2, tzinfo=UTC)
    return TradeRecord(
        symbol="INFY",
        side="BUY",
        qty=1,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        entry_time=now,
        exit_time=now + timedelta(minutes=5),
    )


# ---------------------------------------------------------------------------
# _daily_equity
# ---------------------------------------------------------------------------


def test_daily_equity_empty_returns_empty() -> None:
    df = pl.DataFrame({"date": [], "equity": []}).cast({"equity": pl.Float64})
    result = _daily_equity(df)
    assert len(result) == 0


def test_daily_equity_collapses_intraday_to_last() -> None:
    """Multiple intraday bars on the same day → last equity value is kept."""
    start = datetime(2024, 1, 2, 9, 15, tzinfo=UTC)
    dates = [start + timedelta(minutes=i) for i in range(5)]
    equities = [100.0, 101.0, 99.0, 103.0, 102.0]
    df = pl.DataFrame({"date": dates, "equity": equities})

    daily = _daily_equity(df)
    assert len(daily) == 1
    assert float(daily["equity"][0]) == pytest.approx(102.0)


def test_daily_equity_separate_days_preserved() -> None:
    """One bar per day → all days appear in output."""
    start = datetime(2024, 1, 2, 9, 15, tzinfo=UTC)
    dates = [start + timedelta(days=i) for i in range(4)]
    equities = [100.0, 105.0, 103.0, 110.0]
    df = pl.DataFrame({"date": dates, "equity": equities})

    daily = _daily_equity(df)
    assert len(daily) == 4
    assert list(daily["equity"].to_list()) == pytest.approx(equities)


def test_daily_equity_picks_chronologically_last_on_same_day() -> None:
    """Ensures sort_by within group picks the latest timestamp, not an arbitrary one."""
    start = datetime(2024, 1, 2, 9, 15, tzinfo=UTC)
    # Three bars on the same day; the last bar (chronologically) has equity=50.
    dates = [start, start + timedelta(hours=1), start + timedelta(hours=5)]
    equities = [100.0, 200.0, 50.0]
    df = pl.DataFrame({"date": dates, "equity": equities})

    daily = _daily_equity(df)
    assert float(daily["equity"][0]) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# max_drawdown
# ---------------------------------------------------------------------------


def test_max_drawdown_no_drawdown() -> None:
    """Monotonically rising equity → 0 drawdown."""
    curve = _daily_curve([100.0, 105.0, 110.0, 120.0])
    assert max_drawdown(curve) == pytest.approx(0.0)


def test_max_drawdown_full_ruin() -> None:
    """Equity drops to 0 → drawdown = 1.0 (guarded against division by zero)."""
    # Running max stays at 100; equity drops to 0.
    # Division by zero guard should return 1.0 or close to it.
    curve = _daily_curve([100.0, 80.0, 50.0, 0.01])
    dd = max_drawdown(curve)
    assert 0.0 <= dd <= 1.0


def test_max_drawdown_known_value() -> None:
    """Peak=100, trough=70 → drawdown=(100-70)/100=0.30."""
    curve = _daily_curve([100.0, 90.0, 70.0, 80.0, 95.0])
    assert max_drawdown(curve) == pytest.approx(0.30)


def test_max_drawdown_short_curve_returns_zero() -> None:
    curve = _daily_curve([100.0])
    assert max_drawdown(curve) == pytest.approx(0.0)


def test_max_drawdown_uses_daily_not_intraday_bars() -> None:
    """Intraday noise should not inflate drawdown beyond the daily close-to-close move."""
    # Single day: starts at 100, dips to 50 intraday, recovers to 95 at close.
    start = datetime(2024, 1, 2, 9, 15, tzinfo=UTC)
    dates = [start + timedelta(minutes=i) for i in range(5)]
    # The 50-dip is intraday; daily close is 95.
    # With daily resampling the drawdown is computed day-to-day, not on every bar.
    equities_day1 = [100.0, 50.0, 60.0, 80.0, 95.0]
    day2_start = start + timedelta(days=1)
    equities_day2 = [90.0]
    dates_day2 = [day2_start]
    df = pl.DataFrame({
        "date": dates + dates_day2,
        "equity": equities_day1 + equities_day2,
    })
    dd = max_drawdown(df)
    # Daily: day1 close=95, day2 close=90 → dd=(95-90)/95 ≈ 0.053
    assert dd == pytest.approx((95.0 - 90.0) / 95.0, abs=0.001)


def test_max_drawdown_in_valid_range() -> None:
    """Result must always be in [0, 1] for any reasonable equity curve."""
    import math
    import random
    rng = random.Random(42)
    prices = [1000.0]
    for _ in range(20):
        prices.append(max(prices[-1] * math.exp(rng.gauss(0, 0.03)), 0.01))
    curve = _daily_curve(prices)
    dd = max_drawdown(curve)
    assert 0.0 <= dd <= 1.0


# ---------------------------------------------------------------------------
# max_drawdown_duration
# ---------------------------------------------------------------------------


def test_max_drawdown_duration_no_drawdown() -> None:
    """Monotonically rising curve → 0 duration."""
    curve = _daily_curve([100.0, 110.0, 120.0])
    assert max_drawdown_duration(curve) == timedelta(0)


def test_max_drawdown_duration_simple() -> None:
    """Peak at day0, recovery at day3 → duration = 3 days."""
    start = datetime(2024, 1, 2, tzinfo=UTC)
    curve = _daily_curve([100.0, 90.0, 85.0, 100.0, 105.0], start_date=start)
    dur = max_drawdown_duration(curve)
    # Duration should be from the peak (day0) to recovery (day3) = 3 days.
    assert dur == timedelta(days=3)


def test_max_drawdown_duration_still_in_drawdown_at_end() -> None:
    """If never recovers, duration is measured to the last bar."""
    start = datetime(2024, 1, 2, tzinfo=UTC)
    curve = _daily_curve([100.0, 90.0, 80.0, 70.0], start_date=start)
    dur = max_drawdown_duration(curve)
    assert dur == timedelta(days=3)


def test_max_drawdown_duration_picks_longest() -> None:
    """Multiple drawdown periods → returns the longest one."""
    start = datetime(2024, 1, 2, tzinfo=UTC)
    # Short drawdown: day0→day1 recovery at day2 (2 days)
    # Longer drawdown: day3→day5 recovery at day6 (3 days)
    equities = [100.0, 90.0, 100.0, 110.0, 100.0, 95.0, 110.0, 115.0]
    curve = _daily_curve(equities, start_date=start)
    dur = max_drawdown_duration(curve)
    assert dur == timedelta(days=3)


def test_max_drawdown_duration_short_curve_returns_zero() -> None:
    curve = _daily_curve([100.0])
    assert max_drawdown_duration(curve) == timedelta(0)


def test_max_drawdown_duration_measured_from_peak_not_first_dip() -> None:
    """Duration starts at the peak bar, not at the first bar below the peak."""
    start = datetime(2024, 1, 2, tzinfo=UTC)
    # Peak is at day2 (120), then drawdown starts. Recovery is at day5 (120).
    equities = [100.0, 110.0, 120.0, 100.0, 90.0, 120.0]
    curve = _daily_curve(equities, start_date=start)
    dur = max_drawdown_duration(curve)
    # From peak (day2) to recovery (day5) = 3 days
    assert dur == timedelta(days=3)


# ---------------------------------------------------------------------------
# sharpe_ratio
# ---------------------------------------------------------------------------


def test_sharpe_ratio_short_curve_returns_zero() -> None:
    curve = _daily_curve([100.0])
    assert sharpe_ratio(curve) == pytest.approx(0.0)


def test_sharpe_ratio_zero_variance_returns_zero() -> None:
    curve = _daily_curve([100.0, 100.0, 100.0, 100.0])
    assert sharpe_ratio(curve) == pytest.approx(0.0)


def test_sharpe_ratio_positive_for_steady_growth() -> None:
    """Steady daily growth with no variance → large positive Sharpe."""
    curve = _daily_curve([100.0 + i for i in range(30)])
    assert sharpe_ratio(curve) > 0.0


def test_sharpe_ratio_negative_for_steady_decline() -> None:
    curve = _daily_curve([100.0 - i * 0.5 for i in range(30)])
    assert sharpe_ratio(curve) < 0.0


# ---------------------------------------------------------------------------
# win_rate
# ---------------------------------------------------------------------------


def test_win_rate_empty_trades() -> None:
    assert win_rate([]) == pytest.approx(0.0)


def test_win_rate_all_wins() -> None:
    trades = [_trade(10.0), _trade(5.0), _trade(1.0)]
    assert win_rate(trades) == pytest.approx(1.0)


def test_win_rate_all_losses() -> None:
    trades = [_trade(-10.0), _trade(-5.0)]
    assert win_rate(trades) == pytest.approx(0.0)


def test_win_rate_mixed() -> None:
    trades = [_trade(10.0), _trade(-5.0), _trade(3.0), _trade(-1.0)]
    assert win_rate(trades) == pytest.approx(0.5)


def test_win_rate_zero_pnl_not_counted_as_win() -> None:
    trades = [_trade(0.0), _trade(5.0)]
    assert win_rate(trades) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# profit_factor
# ---------------------------------------------------------------------------


def test_profit_factor_no_trades() -> None:
    assert profit_factor([]) == pytest.approx(0.0)


def test_profit_factor_all_wins() -> None:
    trades = [_trade(10.0), _trade(5.0)]
    assert profit_factor(trades) == float("inf")


def test_profit_factor_all_losses() -> None:
    trades = [_trade(-10.0), _trade(-5.0)]
    assert profit_factor(trades) == pytest.approx(0.0)


def test_profit_factor_known_value() -> None:
    """Gross profit=30, gross loss=10 → factor=3.0."""
    trades = [_trade(20.0), _trade(10.0), _trade(-10.0)]
    assert profit_factor(trades) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# cagr
# ---------------------------------------------------------------------------


def test_cagr_short_curve_returns_zero() -> None:
    curve = _daily_curve([100.0])
    assert cagr(curve, 100.0) == pytest.approx(0.0)


def test_cagr_negative_initial_equity_returns_zero() -> None:
    curve = _daily_curve([100.0, 110.0])
    assert cagr(curve, 0.0) == pytest.approx(0.0)


def test_cagr_total_ruin() -> None:
    curve = _daily_curve([100.0, 1.0])
    result = cagr(curve, 100.0)
    assert result == pytest.approx(-1.0)


def test_cagr_doubling_in_one_year() -> None:
    """Equity doubles in exactly one year → CAGR ≈ 100%."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, tzinfo=UTC)
    dates = [start, end]
    curve = pl.DataFrame({"date": dates, "equity": [100.0, 200.0]})
    result = cagr(curve, 100.0, start=start, end=end)
    assert result == pytest.approx(1.0, abs=0.02)


def test_cagr_flat_equity_near_zero() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, tzinfo=UTC)
    dates = [start, end]
    curve = pl.DataFrame({"date": dates, "equity": [100.0, 100.0]})
    result = cagr(curve, 100.0, start=start, end=end)
    assert result == pytest.approx(0.0, abs=0.001)


# ---------------------------------------------------------------------------
# calmar_ratio
# ---------------------------------------------------------------------------


def test_calmar_ratio_zero_drawdown_returns_zero() -> None:
    curve = _daily_curve([100.0, 105.0, 110.0])
    assert calmar_ratio(curve) == pytest.approx(0.0)


def test_calmar_ratio_positive_for_profitable_low_dd() -> None:
    """Positive CAGR with small drawdown → positive Calmar ratio."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, tzinfo=UTC)
    equities = [100.0 + i * 0.5 for i in range(365)]
    equities[50] = equities[49] * 0.95  # small dip
    dates = [start + timedelta(days=i) for i in range(365)]
    curve = pl.DataFrame({"date": dates, "equity": equities})
    result = calmar_ratio(curve, start=start, end=end)
    assert result > 0.0


# ---------------------------------------------------------------------------
# EquityTracker.snapshot mark-to-market
# ---------------------------------------------------------------------------


def test_equity_tracker_snapshot_uses_current_price_for_open_positions() -> None:
    """Snapshot with current_prices reflects unrealised P&L, not just cost basis."""
    from trading.core.schemas import Side

    from testing.backtesting.portfolio import EquityTracker

    tracker = EquityTracker(initial_equity=100_000.0)
    ts = datetime(2024, 1, 2, 9, 15, tzinfo=UTC)

    # Open a long position: buy 10 @ 1000 → equity debited by 10_000
    tracker.process_fill("INFY", Side.BUY, qty=10, price=1000.0, ts=ts)

    # Market moves up to 1100. Mark-to-market: open value = 10 × 1100 = 11_000
    ts2 = ts + timedelta(minutes=1)
    tracker.mark_snapshot(ts2, current_prices={"INFY": 1100.0})

    curve = tracker.equity_curve
    last_equity = float(curve["equity"][-1])
    # Cash: 100_000 - 10_000 = 90_000. Open value at 1100: 11_000. Total: 101_000.
    assert last_equity == pytest.approx(101_000.0)


def test_equity_tracker_snapshot_without_current_prices_uses_cost_basis() -> None:
    """Calling snapshot() without current_prices falls back to entry price (cost basis)."""
    from trading.core.schemas import Side

    from testing.backtesting.portfolio import EquityTracker

    tracker = EquityTracker(initial_equity=100_000.0)
    ts = datetime(2024, 1, 2, 9, 15, tzinfo=UTC)

    tracker.process_fill("INFY", Side.BUY, qty=10, price=1000.0, ts=ts)

    # Manual snapshot without prices → falls back to entry_price notional = 10_000
    tracker.snapshot(ts + timedelta(minutes=1))

    curve = tracker.equity_curve
    last_equity = float(curve["equity"][-1])
    # Cash: 90_000. Entry notional: 10_000. Total: 100_000.
    assert last_equity == pytest.approx(100_000.0)
