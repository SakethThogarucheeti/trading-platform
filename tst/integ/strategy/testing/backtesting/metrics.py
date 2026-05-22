from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

from testing.backtesting.portfolio import TradeRecord

# ---------------------------------------------------------------------------
# Pure metric functions — no side effects, no I/O.
#
# All functions accept the equity_curve DataFrame with columns:
#   date    — datetime
#   equity  — float
#
# and/or a list[TradeRecord].
# ---------------------------------------------------------------------------


def _daily_equity(equity_curve: pl.DataFrame) -> pl.DataFrame:
    """
    Resample the equity curve to one row per calendar day (last snapshot of
    each day). Days with no fills carry forward the previous day's equity.

    Returns a DataFrame with columns [date (Date), equity (Float64)].
    """
    if len(equity_curve) == 0:
        return equity_curve

    df = equity_curve.with_columns(pl.col("date").dt.date().alias("day"))
    # Last equity value per calendar day
    daily = df.group_by("day").agg(pl.col("equity").last()).sort("day")
    return daily.rename({"day": "date"})


def _daily_returns(equity_curve: pl.DataFrame) -> pl.Series:
    """Compute daily fractional returns, forward-filling missing days."""
    daily = _daily_equity(equity_curve)
    if len(daily) < 2:
        return pl.Series([], dtype=pl.Float64)
    eq = daily["equity"]
    prev = eq.shift(1)
    return ((eq - prev) / prev).drop_nulls()


def sharpe_ratio(equity_curve: pl.DataFrame, risk_free_rate: float = 0.0) -> float:
    """
    Annualised Sharpe ratio computed on **daily** returns (252 trading days/year).

    The equity curve is resampled to one row per calendar day before computing
    returns, so irregular intra-day fill timestamps don't inflate the ratio.

    Returns 0.0 if there are fewer than 2 daily observations or zero variance.
    """
    if len(equity_curve) < 2:
        return 0.0

    rets = _daily_returns(equity_curve)
    if len(rets) < 2:
        return 0.0

    mean_ret = rets.mean()
    std_ret = rets.std()

    if std_ret is None or std_ret == 0.0:
        return 0.0

    excess = float(mean_ret) - risk_free_rate / 252.0
    return float(excess / std_ret * math.sqrt(252.0))


def max_drawdown(equity_curve: pl.DataFrame) -> float:
    """
    Maximum drawdown as a fraction in [0.0, 1.0].

    0.0 means no drawdown ever occurred; 1.0 means total ruin.
    """
    if len(equity_curve) < 2:
        return 0.0

    eq = equity_curve["equity"]
    running_max = eq.cum_max()
    drawdowns = (running_max - eq) / running_max
    return float(drawdowns.max() or 0.0)


def max_drawdown_duration(equity_curve: pl.DataFrame) -> timedelta:
    """
    Longest drawdown period (time from peak to recovery).

    Returns timedelta(0) if there are fewer than 2 rows or no drawdown.
    """
    if len(equity_curve) < 2:
        return timedelta(0)

    dates = equity_curve["date"].to_list()
    equities = equity_curve["equity"].to_list()

    peak_eq = equities[0]
    in_drawdown_since: int | None = None
    max_dur = timedelta(0)

    for i, (ts, eq) in enumerate(zip(dates, equities, strict=False)):
        if eq > peak_eq:
            if in_drawdown_since is not None:
                dur = ts - dates[in_drawdown_since]
                if dur > max_dur:
                    max_dur = dur
                in_drawdown_since = None
            peak_eq = eq
        elif eq < peak_eq and in_drawdown_since is None:
            in_drawdown_since = i

    # If still in drawdown at the end
    if in_drawdown_since is not None:
        dur = dates[-1] - dates[in_drawdown_since]
        if dur > max_dur:
            max_dur = dur

    return max_dur


def win_rate(trades: list[TradeRecord]) -> float:
    """
    Fraction of trades that were profitable (pnl > 0).

    Returns 0.0 if there are no completed trades.
    """
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.pnl > 0)
    return wins / len(trades)


def profit_factor(trades: list[TradeRecord]) -> float:
    """
    Gross profit divided by gross loss.

    Returns float('inf') if there are no losing trades.
    Returns 0.0 if there are no winning trades.
    """
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))

    if gross_loss == 0.0:
        return float("inf") if gross_profit > 0 else 0.0
    if gross_profit == 0.0:
        return 0.0
    return gross_profit / gross_loss


def cagr(
    equity_curve: pl.DataFrame,
    initial_equity: float,
    start: object = None,
    end: object = None,
) -> float:
    """
    Compound annual growth rate.

    ``start`` and ``end`` (datetime-like) pin the backtest period so CAGR is
    computed against the full intended window rather than just the span between
    the first and last fill. When omitted the curve's own date range is used.

    Returns 0.0 if the curve has fewer than 2 rows or time span is 0.
    """
    if len(equity_curve) < 2 or initial_equity <= 0:
        return 0.0

    dates = equity_curve["date"].to_list()
    final_eq = float(equity_curve["equity"][-1])

    period_start = start if start is not None else dates[0]
    period_end = end if end is not None else dates[-1]

    # Handle polars Date vs datetime
    from datetime import date as _date
    from datetime import datetime as _datetime

    def _to_dt(d: object) -> _datetime:
        if isinstance(d, _date) and not isinstance(d, _datetime):
            from datetime import UTC

            return _datetime(d.year, d.month, d.day, tzinfo=UTC)
        return d  # type: ignore[return-value]

    period_start = _to_dt(period_start)
    period_end = _to_dt(period_end)

    years = (period_end - period_start).total_seconds() / (365.25 * 86400)
    if years <= 0:
        return 0.0

    ratio = final_eq / initial_equity
    if ratio <= 0:
        return -1.0  # total ruin — can't take a root of a non-positive number
    return ratio ** (1.0 / years) - 1.0


def calmar_ratio(equity_curve: pl.DataFrame, start: object = None, end: object = None) -> float:
    """
    CAGR divided by maximum drawdown.

    Returns 0.0 if max drawdown is 0 or if CAGR cannot be computed.
    """
    if len(equity_curve) < 2:
        return 0.0

    initial_eq = float(equity_curve["equity"][0])
    _cagr = cagr(equity_curve, initial_eq, start=start, end=end)
    _mdd = max_drawdown(equity_curve)

    if _mdd == 0.0:
        return 0.0
    return _cagr / _mdd
