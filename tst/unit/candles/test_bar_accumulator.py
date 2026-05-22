"""Tests for BarAccumulator — pure OHLCV bar state, no IO."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading.core.schemas import CandleEvent, InstrumentType, TickEvent
from trading.candles.bar_accumulator import BarAccumulator, SymbolConfig

BASE_TIME = datetime(2025, 1, 6, 9, 15, 0, tzinfo=UTC)

_SC = SymbolConfig(symbol="INFY", instrument_token=1, instrument_type=InstrumentType.EQUITY)


def t(offset_seconds: float) -> datetime:
    return BASE_TIME + timedelta(seconds=offset_seconds)


def tick(price: float, ts: datetime, volume: int = 100) -> TickEvent:
    return TickEvent(
        instrument_token=1,
        instrument_type=InstrumentType.EQUITY,
        last_price=price,
        volume=volume,
        timestamp=ts,
        tick_log_id=0,
    )


# ---------------------------------------------------------------------------
# First tick
# ---------------------------------------------------------------------------


def test_first_tick_returns_none() -> None:
    acc = BarAccumulator()
    result = acc.process(_SC, "1min", tick(100.0, t(0)))
    assert result is None


def test_first_tick_initialises_bar() -> None:
    acc = BarAccumulator()
    acc.process(_SC, "1min", tick(100.0, t(0)))
    bar = acc._bars[("INFY", "1min")]
    assert bar.open == bar.high == bar.low == bar.close == 100.0
    assert bar.volume == 100


# ---------------------------------------------------------------------------
# Tick within the same bar
# ---------------------------------------------------------------------------


def test_higher_price_updates_high() -> None:
    acc = BarAccumulator()
    acc.process(_SC, "1min", tick(100.0, t(0)))
    acc.process(_SC, "1min", tick(110.0, t(10)))
    bar = acc._bars[("INFY", "1min")]
    assert bar.high == 110.0
    assert bar.low == 100.0
    assert bar.close == 110.0


def test_lower_price_updates_low() -> None:
    acc = BarAccumulator()
    acc.process(_SC, "1min", tick(100.0, t(0)))
    acc.process(_SC, "1min", tick(90.0, t(10)))
    bar = acc._bars[("INFY", "1min")]
    assert bar.low == 90.0
    assert bar.high == 100.0


def test_volume_accumulates() -> None:
    acc = BarAccumulator()
    acc.process(_SC, "1min", tick(100.0, t(0), volume=50))
    acc.process(_SC, "1min", tick(101.0, t(10), volume=75))
    bar = acc._bars[("INFY", "1min")]
    assert bar.volume == 125


# ---------------------------------------------------------------------------
# Bar boundary — returns CandleEvent
# ---------------------------------------------------------------------------


def test_tick_in_new_bar_returns_candle_event() -> None:
    acc = BarAccumulator()
    acc.process(_SC, "1min", tick(100.0, BASE_TIME))
    acc.process(_SC, "1min", tick(105.0, BASE_TIME + timedelta(seconds=30)))
    candle = acc.process(_SC, "1min", tick(110.0, BASE_TIME + timedelta(minutes=1)))

    assert isinstance(candle, CandleEvent)
    assert candle.open == 100.0
    assert candle.high == 105.0
    assert candle.low == 100.0
    assert candle.close == 105.0
    assert candle.symbol == "INFY"
    assert candle.interval == "1min"


def test_candle_timestamp_is_bar_close() -> None:
    acc = BarAccumulator()
    acc.process(_SC, "1min", tick(100.0, BASE_TIME))
    candle = acc.process(_SC, "1min", tick(101.0, BASE_TIME + timedelta(minutes=1)))

    assert candle is not None
    assert candle.timestamp == BASE_TIME + timedelta(minutes=1)


# ---------------------------------------------------------------------------
# Multiple intervals — independent bar state per (symbol, interval)
# ---------------------------------------------------------------------------


def test_multiple_intervals_track_independently() -> None:
    acc = BarAccumulator()
    acc.process(_SC, "1min", tick(100.0, BASE_TIME))
    acc.process(_SC, "5min", tick(100.0, BASE_TIME))

    # 1min crosses at BASE_TIME + 1min; 5min still open
    candle_1min = acc.process(_SC, "1min", tick(110.0, BASE_TIME + timedelta(minutes=1)))
    candle_5min = acc.process(_SC, "5min", tick(110.0, BASE_TIME + timedelta(minutes=1)))

    assert candle_1min is not None
    assert candle_1min.interval == "1min"
    assert candle_5min is None  # still within the 9:15–9:20 window


def test_5min_bar_closes_at_correct_boundary() -> None:
    acc = BarAccumulator()
    acc.process(_SC, "5min", tick(100.0, BASE_TIME))
    # BASE_TIME is 09:15; next 5min bar opens at 09:20
    candle = acc.process(_SC, "5min", tick(200.0, BASE_TIME + timedelta(minutes=5)))

    assert candle is not None
    assert candle.interval == "5min"
    assert candle.open == 100.0
