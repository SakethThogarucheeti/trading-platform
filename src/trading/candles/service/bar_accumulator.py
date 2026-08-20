from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

from trading.candles.api.schemas import CandleEvent
from trading.core.schemas import InstrumentType
from trading.tick_ingest.api.schemas import TickEvent

INTERVAL_MINUTES: dict[str, int] = {
    "1min": 1,
    "3min": 3,
    "5min": 5,
    "10min": 10,
    "15min": 15,
    "30min": 30,
    "60min": 60,
}


@dataclass
class PartialBar:
    symbol: str
    instrument_type: InstrumentType
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: int  # latest cumulative day volume observed in this bar
    open_cumulative_volume: int  # cumulative day volume as of this bar's first tick
    bar_open_time: datetime
    tick_log_id: int


@dataclass
class SymbolConfig:
    symbol: str
    instrument_token: int
    instrument_type: InstrumentType


def bar_open_time(ts: datetime, interval: str) -> datetime:
    minutes = INTERVAL_MINUTES.get(interval, 1)
    total_minutes = ts.hour * 60 + ts.minute
    floored = (total_minutes // minutes) * minutes
    bar_hour, bar_minute = divmod(floored, 60)
    return ts.replace(hour=bar_hour, minute=bar_minute, second=0, microsecond=0)


def _new_bar(sc: SymbolConfig, interval: str, event: TickEvent, bar_open: datetime) -> PartialBar:
    return PartialBar(
        symbol=sc.symbol,
        instrument_type=sc.instrument_type,
        interval=interval,
        open=event.last_price,
        high=event.last_price,
        low=event.last_price,
        close=event.last_price,
        volume=event.volume,
        open_cumulative_volume=event.volume,
        bar_open_time=bar_open,
        tick_log_id=event.tick_log_id,
    )


class AbstractBarAccumulator(ABC):
    """Interface for OHLCV bar state machines."""

    @abstractmethod
    def process(self, sc: SymbolConfig, interval: str, tick: TickEvent) -> CandleEvent | None:
        """Update bar state for one tick. Returns a CandleEvent on bar close, else None."""


class BarAccumulator(AbstractBarAccumulator):
    """
    Pure in-memory OHLCV bar state. No IO, no async, no dependencies.

    Zerodha ticks carry ``volume`` as the cumulative quantity traded for the
    whole day (Kite's ``volume_traded`` field), not a per-tick increment.
    ``PartialBar.volume`` tracks the latest cumulative value seen in the
    current bar; each bar's traded volume is the delta between that and
    ``open_cumulative_volume`` (the cumulative value as of the bar's first
    tick), computed in ``_close_bar``. This must be captured per-bar at
    open time, not carried over from the previous bar's own last-known
    value — the tick that closes a bar belongs to (and opens) the *next*
    bar and carries a fresher cumulative reading than anything the closing
    bar itself observed.
    """

    def __init__(self) -> None:
        self._bars: dict[tuple[str, str], PartialBar] = {}

    def process(self, sc: SymbolConfig, interval: str, tick: TickEvent) -> CandleEvent | None:
        key = (sc.symbol, interval)
        bar_open = bar_open_time(tick.timestamp, interval)
        existing = self._bars.get(key)

        if existing is None:
            self._bars[key] = _new_bar(sc, interval, tick, bar_open)
            return None

        if bar_open > existing.bar_open_time:
            existing.tick_log_id = tick.tick_log_id
            candle = self._close_bar(existing)
            self._bars[key] = _new_bar(sc, interval, tick, bar_open)
            return candle

        existing.high = max(existing.high, tick.last_price)
        existing.low = min(existing.low, tick.last_price)
        existing.close = tick.last_price
        existing.volume = tick.volume  # cumulative day volume — overwrite, don't sum
        existing.tick_log_id = tick.tick_log_id
        return None

    def _close_bar(self, bar: PartialBar) -> CandleEvent:
        minutes = INTERVAL_MINUTES.get(bar.interval, 1)
        bar_close = bar.bar_open_time + timedelta(minutes=minutes)
        bar_volume = max(bar.volume - bar.open_cumulative_volume, 0)
        return CandleEvent(
            symbol=bar.symbol,
            instrument_type=bar.instrument_type,
            interval=bar.interval,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar_volume,
            timestamp=bar_close,
            tick_log_id=bar.tick_log_id,
        )
