from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from trading.broker.base.broker import Broker
from trading.candles.bar_accumulator import INTERVAL_MINUTES, SymbolConfig
from trading.core.clock import Clock, SystemClock
from trading.core.schemas import CandleEvent, InstrumentType
from quantindicators.types import CandleRow
from trading.storage.stores.candle import AbstractCandleDataStore

logger = logging.getLogger(__name__)

_CALENDAR_MINUTES_PER_TRADING_MINUTE = (7 / 5) * (1440 / 375)  # ≈ 5.4


def _ensure_utc(dt: object) -> datetime:
    if isinstance(dt, datetime):
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    raise TypeError(f"Expected datetime, got {type(dt)}")


class WarmupResult(NamedTuple):
    candles: list[CandleEvent]
    fetch_failures: int
    parse_failures: int
    persist_failures: int


class CandleWarmer:
    """
    Fetches historical candles from the broker and persists them to the candle store.

    Returns a WarmupResult with the CandleEvent list and per-phase failure counts
    so callers can decide whether degraded warmup is acceptable.
    """

    def __init__(
        self,
        symbols: list[SymbolConfig],
        intervals: list[str],
        warmup_count: int,
        broker: Broker,
        candle_store: AbstractCandleDataStore,
        clock: Clock | None = None,
    ) -> None:
        self._symbols = symbols
        self._intervals = intervals
        self._warmup_count = warmup_count
        self._broker = broker
        self._candle_store = candle_store
        self._clock: Clock = clock or SystemClock()

    async def fetch(self) -> WarmupResult:
        """Fetch historical candles, persist them, and return as CandleEvents (tick_log_id=0)."""
        events: list[CandleEvent] = []
        now = self._clock.now()
        max_minutes = max(
            (INTERVAL_MINUTES.get(iv, 1) for iv in self._intervals), default=1
        )
        trading_minutes_needed = self._warmup_count * max_minutes
        calendar_minutes = trading_minutes_needed * _CALENDAR_MINUTES_PER_TRADING_MINUTE
        lookback_hours = int(calendar_minutes / 60) + 24
        start = now - timedelta(hours=lookback_hours)

        fetch_failures = parse_failures = persist_failures = 0

        for sc in self._symbols:
            for interval in self._intervals:
                try:
                    df = self._broker.get_ohlc(sc.symbol, interval, start, now)
                except Exception as exc:
                    logger.warning(
                        "CandleWarmer: fetch failed for %s %s — %s", sc.symbol, interval, exc
                    )
                    fetch_failures += 1
                    continue
                if df.is_empty():
                    continue
                warmup_rows: list[CandleRow] = []
                for row in df.tail(self._warmup_count).iter_rows(named=True):
                    try:
                        ts = _ensure_utc(row["date"])
                        events.append(
                            CandleEvent(
                                symbol=sc.symbol,
                                instrument_type=sc.instrument_type,
                                interval=interval,
                                open=float(row["open"]),
                                high=float(row["high"]),
                                low=float(row["low"]),
                                close=float(row["close"]),
                                volume=int(row.get("volume", 0)),
                                timestamp=ts,
                                tick_log_id=0,
                            )
                        )
                        warmup_rows.append(
                            CandleRow(
                                symbol=sc.symbol,
                                interval=interval,
                                ts=ts,
                                open=float(row["open"]),
                                high=float(row["high"]),
                                low=float(row["low"]),
                                close=float(row["close"]),
                                volume=int(row.get("volume", 0)),
                            )
                        )
                    except Exception as exc:
                        logger.warning(
                            "CandleWarmer: invalid row %s %s — %s", sc.symbol, interval, exc
                        )
                        parse_failures += 1
                if warmup_rows:
                    try:
                        await self._candle_store.save_candles(warmup_rows)
                    except Exception as exc:
                        logger.warning(
                            "CandleWarmer: persist failed for %s %s — %s", sc.symbol, interval, exc
                        )
                        persist_failures += 1

        if fetch_failures or parse_failures or persist_failures:
            logger.warning(
                "CandleWarmer: finished with errors — fetch=%d parse=%d persist=%d",
                fetch_failures,
                parse_failures,
                persist_failures,
            )
        logger.info("CandleWarmer: produced %d candles", len(events))
        return WarmupResult(
            candles=events,
            fetch_failures=fetch_failures,
            parse_failures=parse_failures,
            persist_failures=persist_failures,
        )
