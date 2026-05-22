from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import polars as pl

# ---------------------------------------------------------------------------
# Synthetic OHLCV data generators
#
# All return pl.DataFrame with columns [date, open, high, low, close, volume]
# matching the DataLoader contract. All prices are > 0, volume >= 0.
# ---------------------------------------------------------------------------


def _bar_times(
    n_bars: int,
    start: datetime | None,
    interval_mins: int,
) -> list[datetime]:
    if start is None:
        start = datetime(2024, 1, 2, 9, 15, 0, tzinfo=UTC)
    return [start + timedelta(minutes=i * interval_mins) for i in range(n_bars)]


def random_walk_ohlcv(
    n_bars: int,
    start_price: float = 1000.0,
    volatility: float = 0.01,
    seed: int | None = None,
    start: datetime | None = None,
    interval_mins: int = 1,
) -> pl.DataFrame:
    """
    Generate a random-walk OHLCV DataFrame.

    Each bar's close is a log-normal step from the previous close:
    ``close = prev_close * exp(N(0, volatility))``.
    H/L are generated symmetrically around the bar midpoint.

    Parameters
    ----------
    n_bars:
        Number of bars to generate.
    start_price:
        Opening price of the first bar.
    volatility:
        Per-bar volatility (std of log returns). 0.01 ≈ 1% daily vol.
    seed:
        RNG seed for reproducibility.
    start:
        Timestamp of the first bar. Defaults to 2024-01-02 09:15 UTC.
    interval_mins:
        Minutes between bars.
    """
    rng = random.Random(seed)
    import math

    times = _bar_times(n_bars, start, interval_mins)
    rows: list[tuple[datetime, float, float, float, float, int]] = []
    price = start_price

    for ts in times:
        log_return = rng.gauss(0.0, volatility)
        close = price * math.exp(log_return)
        open_ = price
        spread = abs(close - open_) * (1 + rng.uniform(0, 0.5))
        high = max(open_, close) + spread * rng.uniform(0.1, 0.5)
        low = min(open_, close) - spread * rng.uniform(0.1, 0.5)
        low = max(low, 0.01)  # price must be > 0
        volume = rng.randint(1000, 50000)
        rows.append((ts, round(open_, 4), round(high, 4), round(low, 4), round(close, 4), volume))
        price = close

    return pl.DataFrame(
        rows,
        schema=["date", "open", "high", "low", "close", "volume"],
        orient="row",
    )


def crash_scenario(
    n_bars: int,
    crash_bar: int,
    crash_pct: float = 0.30,
    start_price: float = 1000.0,
    volatility: float = 0.005,
    seed: int | None = None,
    start: datetime | None = None,
    interval_mins: int = 1,
) -> pl.DataFrame:
    """
    Generate OHLCV data with a sudden price crash at ``crash_bar``.

    Prices follow a random walk until ``crash_bar``, then drop by
    ``crash_pct`` instantly, and continue the walk from the lower level.

    Parameters
    ----------
    crash_bar:
        0-indexed bar at which the crash occurs.
    crash_pct:
        Fraction of price to drop at the crash bar (0.30 = 30% drop).
    """
    import math

    rng = random.Random(seed)
    times = _bar_times(n_bars, start, interval_mins)
    rows: list[tuple[datetime, float, float, float, float, int]] = []
    price = start_price

    for i, ts in enumerate(times):
        if i == crash_bar:
            price *= 1 - crash_pct

        log_return = rng.gauss(0.0, volatility)
        close = price * math.exp(log_return)
        open_ = price
        spread = abs(close - open_) * (1 + rng.uniform(0, 0.5))
        high = max(open_, close) + spread * rng.uniform(0.1, 0.5)
        low = min(open_, close) - spread * rng.uniform(0.1, 0.5)
        low = max(low, 0.01)
        volume = rng.randint(5000, 200_000) if i == crash_bar else rng.randint(1000, 50000)
        rows.append((ts, round(open_, 4), round(high, 4), round(low, 4), round(close, 4), volume))
        price = close

    return pl.DataFrame(
        rows,
        schema=["date", "open", "high", "low", "close", "volume"],
        orient="row",
    )


def volatility_spike(
    n_bars: int,
    spike_bar: int,
    spike_multiplier: float = 3.0,
    start_price: float = 1000.0,
    volatility: float = 0.01,
    seed: int | None = None,
    start: datetime | None = None,
    interval_mins: int = 1,
) -> pl.DataFrame:
    """
    Generate OHLCV data with a volatility spike at ``spike_bar``.

    Bars around ``spike_bar`` have volatility multiplied by
    ``spike_multiplier``. The spike lasts for 5 bars.
    """
    import math

    rng = random.Random(seed)
    times = _bar_times(n_bars, start, interval_mins)
    rows: list[tuple[datetime, float, float, float, float, int]] = []
    price = start_price

    for i, ts in enumerate(times):
        bar_vol = volatility * spike_multiplier if abs(i - spike_bar) <= 2 else volatility
        log_return = rng.gauss(0.0, bar_vol)
        close = price * math.exp(log_return)
        open_ = price
        spread = abs(close - open_) * (1 + rng.uniform(0, 0.5))
        high = max(open_, close) + spread * rng.uniform(0.1, 0.5)
        low = min(open_, close) - spread * rng.uniform(0.1, 0.5)
        low = max(low, 0.01)
        volume = rng.randint(1000, 50000)
        rows.append((ts, round(open_, 4), round(high, 4), round(low, 4), round(close, 4), volume))
        price = close

    return pl.DataFrame(
        rows,
        schema=["date", "open", "high", "low", "close", "volume"],
        orient="row",
    )


def trending_market(
    n_bars: int,
    drift: float = 0.0005,
    start_price: float = 1000.0,
    volatility: float = 0.005,
    seed: int | None = None,
    start: datetime | None = None,
    interval_mins: int = 1,
) -> pl.DataFrame:
    """
    Generate OHLCV data with an upward trend.

    Positive ``drift`` creates an uptrend; negative drift a downtrend.
    ``drift`` is the per-bar log-return mean added to the random walk.
    """
    import math

    rng = random.Random(seed)
    times = _bar_times(n_bars, start, interval_mins)
    rows: list[tuple[datetime, float, float, float, float, int]] = []
    price = start_price

    for ts in times:
        log_return = rng.gauss(drift, volatility)
        close = price * math.exp(log_return)
        open_ = price
        spread = abs(close - open_) * (1 + rng.uniform(0, 0.5))
        high = max(open_, close) + spread * rng.uniform(0.1, 0.5)
        low = min(open_, close) - spread * rng.uniform(0.1, 0.5)
        low = max(low, 0.01)
        volume = rng.randint(1000, 50000)
        rows.append((ts, round(open_, 4), round(high, 4), round(low, 4), round(close, 4), volume))
        price = close

    return pl.DataFrame(
        rows,
        schema=["date", "open", "high", "low", "close", "volume"],
        orient="row",
    )
