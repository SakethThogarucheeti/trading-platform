"""Tests for CandleAggregator handle() — tick accumulation into candles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import build_session_factory, init_db
from trading.core.models import Instrument
from trading.core.schemas import CandleEvent, InstrumentType, TickEvent
from trading.candles.service.bar_accumulator import bar_open_time
from trading.candles.service.aggregator import CandleAggregator
from trading.candles.service.persister import CandleConfig, CandlePersister
from trading.tick_ingest.storage.store import AuditStore
from trading.candles.storage.store import CandleDataStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2025, 1, 6, 9, 15, 0, tzinfo=UTC)  # Monday 09:15 IST


def t(offset_seconds: float) -> datetime:
    """Return a timestamp offset from BASE_TIME."""
    return BASE_TIME + timedelta(seconds=offset_seconds)


def tick(token: int, price: float, ts: datetime, volume: int = 100) -> TickEvent:
    return TickEvent(
        instrument_token=token,
        instrument_type=InstrumentType.EQUITY,
        last_price=price,
        volume=volume,
        timestamp=ts,
        tick_log_id=0,
    )


def make_instrument(token: int, symbol: str = None) -> Instrument:
    return Instrument(
        token=token,
        symbol=symbol or f"SYM{token}",
        exchange="NSE",
        instrument_type="EQUITY",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


class _NullLogger:
    async def log(self, event: CandleEvent) -> None:
        pass


def make_registry(
    engine: AsyncEngine,
    tokens: list[int] | None = None,
    intervals: list[str] | None = None,
    warmup_count: int = 5,
) -> CandleAggregator:
    tokens = tokens or [1]
    intervals = intervals or ["1min"]
    instruments = [make_instrument(t) for t in tokens]
    config = CandleConfig(
        instruments=instruments,
        intervals=intervals,
        warmup_count=warmup_count,
    )
    return CandleAggregator(config=config, candle_logger=_NullLogger())


# ---------------------------------------------------------------------------
# Bar open time helper
# ---------------------------------------------------------------------------


def testbar_open_time_1min() -> None:
    ts = datetime(2025, 1, 6, 9, 17, 35, tzinfo=UTC)
    assert bar_open_time(ts, "1min") == datetime(2025, 1, 6, 9, 17, 0, tzinfo=UTC)


def testbar_open_time_5min() -> None:
    ts = datetime(2025, 1, 6, 9, 17, 0, tzinfo=UTC)
    assert bar_open_time(ts, "5min") == datetime(2025, 1, 6, 9, 15, 0, tzinfo=UTC)


def testbar_open_time_15min() -> None:
    ts = datetime(2025, 1, 6, 9, 29, 59, tzinfo=UTC)
    assert bar_open_time(ts, "15min") == datetime(2025, 1, 6, 9, 15, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Bar aggregation — direct registry tests
# ---------------------------------------------------------------------------


async def test_first_tick_initialises_bar(engine: AsyncEngine) -> None:
    reg = make_registry(engine)

    result = await reg.handle(tick(1, 100.0, t(0)))
    # First tick opens the bar but doesn't close it
    assert result is None
    assert ("SYM1", "1min") in reg._accumulator._bars
    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.open == bar.high == bar.low == bar.close == 100.0
    assert bar.volume == 100


async def test_higher_price_tick_updates_high(engine: AsyncEngine) -> None:
    reg = make_registry(engine)

    await reg.handle(tick(1, 100.0, t(0)))
    await reg.handle(tick(1, 110.0, t(10)))

    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.high == 110.0
    assert bar.low == 100.0
    assert bar.close == 110.0


async def test_lower_price_tick_updates_low(engine: AsyncEngine) -> None:
    reg = make_registry(engine)

    await reg.handle(tick(1, 100.0, t(0)))
    await reg.handle(tick(1, 90.0, t(10)))

    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.low == 90.0
    assert bar.high == 100.0


async def test_volume_accumulates_within_bar(engine: AsyncEngine) -> None:
    reg = make_registry(engine)

    await reg.handle(tick(1, 100.0, t(0), volume=50))
    await reg.handle(tick(1, 101.0, t(10), volume=75))

    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.volume == 125


async def test_tick_crossing_bar_boundary_emits_candle(engine: AsyncEngine) -> None:
    reg = make_registry(engine)

    # Both at 09:15 — same bar
    await reg.handle(tick(1, 100.0, BASE_TIME))
    await reg.handle(tick(1, 105.0, BASE_TIME + timedelta(seconds=30)))
    # 09:16 — new bar → emits previous
    candle = await reg.handle(tick(1, 110.0, BASE_TIME + timedelta(minutes=1)))

    assert candle is not None
    assert candle.open == 100.0
    assert candle.high == 105.0
    assert candle.close == 105.0
    assert candle.symbol == "SYM1"
    assert candle.interval == "1min"


async def test_two_intervals_first_closes_1min(engine: AsyncEngine) -> None:
    reg = make_registry(engine, intervals=["1min", "5min"])

    # Two ticks in the 09:15 window
    await reg.handle(tick(1, 100.0, BASE_TIME))
    await reg.handle(tick(1, 102.0, BASE_TIME + timedelta(seconds=30)))

    # 09:16 — crosses 1min but stays in 5min
    candle = await reg.handle(tick(1, 104.0, BASE_TIME + timedelta(minutes=1)))

    # 1min bar was the first interval checked, so it returns the closed bar
    assert candle is not None
    assert candle.interval == "1min"

    # Both bars still tracked
    assert ("SYM1", "1min") in reg._accumulator._bars
    assert ("SYM1", "5min") in reg._accumulator._bars


async def test_zero_volume_tick_updates_ohlc(engine: AsyncEngine) -> None:
    """volume=0 is valid (Zerodha sends it on auction)."""
    reg = make_registry(engine)

    await reg.handle(tick(1, 100.0, t(0), volume=0))
    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.volume == 0
    assert bar.open == 100.0


async def test_unknown_token_returns_none(engine: AsyncEngine) -> None:
    reg = make_registry(engine, tokens=[1])

    result = await reg.handle(tick(999, 100.0, t(0)))
    assert result is None


async def test_candle_persister_tick_log_id_positive_calls_audit(engine: AsyncEngine) -> None:
    """CandlePersister.log calls audit.log_decision when tick_log_id > 0."""
    from trading.candles.api.interfaces import AbstractCandleStore as AbstractCandleDataStore

    class _SucceedingCandleStore(AbstractCandleDataStore):
        async def save_candles(self, rows) -> None:
            pass

        async def get_candles(self, symbol, interval, limit):
            return []

        async def get_candles_since(self, symbol, interval, since):
            return []

    sf = build_session_factory(engine)
    persister = CandlePersister(candle=_SucceedingCandleStore(), audit=AuditStore(sf))

    candle_event = CandleEvent(
        symbol="SYM1",
        instrument_type=InstrumentType.EQUITY,
        interval="1min",
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000,
        timestamp=BASE_TIME,
        tick_log_id=99,
    )
    await persister.log(candle_event)


async def test_candle_persister_exception_path_is_swallowed(engine: AsyncEngine) -> None:
    """CandlePersister.log exception is caught and logged, not re-raised."""
    from trading.candles.api.interfaces import AbstractCandleStore as AbstractCandleDataStore

    class _FailingCandleStore(AbstractCandleDataStore):
        async def save_candles(self, rows) -> None:
            raise RuntimeError("candle save failed")

        async def get_candles(self, symbol, interval, limit):
            return []

        async def get_candles_since(self, symbol, interval, since):
            return []

    sf = build_session_factory(engine)
    persister = CandlePersister(candle=_FailingCandleStore(), audit=AuditStore(sf))

    candle_event = CandleEvent(
        symbol="SYM1",
        instrument_type=InstrumentType.EQUITY,
        interval="1min",
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000,
        timestamp=BASE_TIME,
        tick_log_id=99,
    )
    await persister.log(candle_event)


async def test_handle_with_tick_log_id_schedules_log_candle(engine: AsyncEngine) -> None:
    """tick_log_id != 0 triggers fire-and-forget _log_candle."""
    reg = make_registry(engine, tokens=[1], intervals=["1min"])

    from trading.app.database import get_session

    async with get_session(engine) as s:
        s.add(Instrument(token=1, symbol="SYM1", exchange="NSE", instrument_type="EQUITY"))

    t1 = tick(1, 100.0, BASE_TIME, volume=100)
    t2 = TickEvent(
        instrument_token=1,
        instrument_type=InstrumentType.EQUITY,
        last_price=101.0,
        volume=50,
        timestamp=BASE_TIME + timedelta(minutes=1, seconds=1),
        tick_log_id=99,
    )

    await reg.handle(t1)
    await reg.handle(t2)
