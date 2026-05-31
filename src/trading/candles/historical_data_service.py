from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import polars as pl
from quantindicators.types import CandleRow

from trading.broker.base.broker import Broker
from trading.candles.bar_accumulator import INTERVAL_MINUTES
from trading.core.clock import Clock, SystemClock
from trading.storage.stores.candle import AbstractCandleDataStore

logger = logging.getLogger(__name__)

# Trading calendar approximation: 375 min/day, 5 days/week → ~5.4 calendar min per trading min
_CALENDAR_MINUTES_PER_TRADING_MINUTE = (7 / 5) * (1440 / 375)


@dataclass
class HistoricalDataResult:
    """Returned by HistoricalDataService.fetch()."""

    df: pl.DataFrame
    fetched_from_broker: bool


class HistoricalDataService:
    """
    Single source of truth for historical OHLCV bars.

    Fetch strategy:
      1. Query DB for bars in [start, end].
      2. If DB covers the full range, return those rows.
      3. Otherwise call broker.get_ohlc(), persist new rows, return broker result.
    """

    def __init__(
        self,
        broker: Broker,
        candle_store: AbstractCandleDataStore,
        clock: Clock | None = None,
    ) -> None:
        self._broker = broker
        self._candle_store = candle_store
        self._clock: Clock = clock or SystemClock()

    async def fetch(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> HistoricalDataResult:
        """
        Return OHLCV bars for symbol/interval in [start, end].
        Checks DB first; calls broker only if DB coverage is insufficient.
        Persists any broker-sourced rows back to DB.
        """
        rows = await self._candle_store.get_candles_since(symbol, interval, since=start)
        in_range = [r for r in rows if r["ts"] <= end]

        interval_minutes = INTERVAL_MINUTES.get(interval, 1)
        if _has_full_coverage(in_range, start, end, interval_minutes):
            logger.debug(
                "HistoricalDataService: DB hit %s %s [%s, %s] (%d rows)",
                symbol, interval, start.date(), end.date(), len(in_range),
            )
            return HistoricalDataResult(df=_rows_to_df(in_range), fetched_from_broker=False)

        logger.debug(
            "HistoricalDataService: broker fetch %s %s [%s, %s]",
            symbol, interval, start.date(), end.date(),
        )
        try:
            df = self._broker.get_ohlc(symbol, interval, start, end)
        except Exception:
            logger.warning(
                "HistoricalDataService: broker fetch failed for %s %s", symbol, interval,
                exc_info=True,
            )
            return HistoricalDataResult(df=_rows_to_df([]), fetched_from_broker=True)
        if not df.is_empty():
            candle_rows = _df_to_candle_rows(symbol, interval, df)
            try:
                await self._candle_store.save_candles(candle_rows)
            except Exception:
                logger.warning(
                    "HistoricalDataService: persist failed for %s %s", symbol, interval,
                    exc_info=True,
                )
        return HistoricalDataResult(df=df, fetched_from_broker=True)


def warmup_start(now: datetime, intervals: list[str], warmup_count: int) -> datetime:
    """
    Compute how far back to look to guarantee `warmup_count` bars for every interval.

    Converts trading minutes to calendar minutes using an IST market approximation
    (375 trading minutes/day, 5 days/week).
    """
    max_minutes = max((INTERVAL_MINUTES.get(iv, 1) for iv in intervals), default=1)
    trading_minutes_needed = warmup_count * max_minutes
    calendar_minutes = trading_minutes_needed * _CALENDAR_MINUTES_PER_TRADING_MINUTE
    lookback_hours = int(calendar_minutes / 60) + 24
    return now - timedelta(hours=lookback_hours)


# ---------------------------------------------------------------------------
# Module-private helpers (free functions — independently testable)
# ---------------------------------------------------------------------------


def _has_full_coverage(
    rows: list[CandleRow],
    start: datetime,
    end: datetime,
    interval_minutes: int,
) -> bool:
    """
    Returns True when `rows` covers [start, end] without gaps at the boundaries.

    Tolerates normal market gaps (weekends, holidays) — only checks that the
    first and last rows are within one interval-width of the requested endpoints.
    Does NOT require every minute to be present.
    """
    if not rows:
        return False
    tolerance = timedelta(minutes=interval_minutes)
    first_ts = rows[0]["ts"]
    last_ts = rows[-1]["ts"]
    return first_ts <= start + tolerance and last_ts >= end - tolerance


def _rows_to_df(rows: list[CandleRow]) -> pl.DataFrame:
    """Convert a list of CandleRow TypedDicts to a Polars DataFrame."""
    if not rows:
        return pl.DataFrame(
            schema={
                "date": pl.Datetime("us", "UTC"),
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Int64,
            }
        )
    return pl.DataFrame(
        {
            "date": [r["ts"] for r in rows],
            "open": [r["open"] for r in rows],
            "high": [r["high"] for r in rows],
            "low": [r["low"] for r in rows],
            "close": [r["close"] for r in rows],
            "volume": [r["volume"] for r in rows],
        }
    ).with_columns(pl.col("date").dt.replace_time_zone("UTC"))


def _df_to_candle_rows(symbol: str, interval: str, df: pl.DataFrame) -> list[CandleRow]:
    """Convert a broker OHLCV DataFrame to a list of CandleRow TypedDicts."""
    rows: list[CandleRow] = []
    for row in df.iter_rows(named=True):
        ts: datetime = row["date"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        rows.append(
            CandleRow(
                symbol=symbol,
                interval=interval,
                ts=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume", 0)),
            )
        )
    return rows
