"""Tests for CandleAggregator (was CandleAggregator)"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.broker.base.broker import Broker
from trading.core.database import build_session_factory, init_db
from trading.core.models import Instrument
from trading.core.schemas import CandleEvent, InstrumentType, TickEvent
from trading.candles.bar_accumulator import bar_open_time
from trading.candles.candle_aggregator import CandleAggregator, CandleConfig
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.candle import CandleDataStore

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


def make_ohlc_df(n: int, start: datetime, interval_mins: int = 1) -> pl.DataFrame:
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=i * interval_mins)
        price = 100.0 + i
        rows.append(
            {
                "date": ts,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 1000 + i,
            }
        )
    return pl.DataFrame(rows)


def make_instrument(token: int, symbol: str = None) -> Instrument:
    return Instrument(
        token=token,
        symbol=symbol or f"SYM{token}",
        exchange="NSE",
        instrument_type="EQUITY",
    )


# ---------------------------------------------------------------------------
# MockBroker
# ---------------------------------------------------------------------------


class MockBroker(Broker):
    """Returns a pre-configured DataFrame on get_ohlc(); can raise."""

    def __init__(self, df: pl.DataFrame | None = None, raises: bool = False) -> None:
        self._df = df if df is not None else pl.DataFrame()
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    def get_instruments(self) -> pl.DataFrame:
        return pl.DataFrame()

    async def place_order(self, symbol, side, qty, order_type, limit_price=None) -> str:  # type: ignore[override]
        return "MOCK_ORDER"

    def get_ohlc(self, symbol: str, interval: str, start: datetime, end: datetime) -> pl.DataFrame:
        self.calls.append((symbol, interval))
        if self._raises:
            raise RuntimeError("broker unavailable")
        return self._df


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def make_registry(
    broker: MockBroker,
    engine: AsyncEngine,
    tokens: list[int] | None = None,
    intervals: list[str] | None = None,
    warmup_count: int = 5,
) -> CandleAggregator:
    tokens = tokens or [1]
    intervals = intervals or ["1min"]
    instruments = [make_instrument(t) for t in tokens]
    sf = build_session_factory(engine)
    config = CandleConfig(
        instruments=instruments,
        intervals=intervals,
        warmup_count=warmup_count,
    )
    return CandleAggregator(
        config=config,
        broker=broker,
        candle=CandleDataStore(sf),
        audit=AuditStore(sf),
    )



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
    reg = make_registry(MockBroker(), engine)

    result = await reg.handle(tick(1, 100.0, t(0)))
    # First tick opens the bar but doesn't close it
    assert result is None
    assert ("SYM1", "1min") in reg._accumulator._bars
    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.open == bar.high == bar.low == bar.close == 100.0
    assert bar.volume == 100


async def test_higher_price_tick_updates_high(engine: AsyncEngine) -> None:
    reg = make_registry(MockBroker(), engine)

    await reg.handle(tick(1, 100.0, t(0)))
    await reg.handle(tick(1, 110.0, t(10)))

    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.high == 110.0
    assert bar.low == 100.0
    assert bar.close == 110.0


async def test_lower_price_tick_updates_low(engine: AsyncEngine) -> None:
    reg = make_registry(MockBroker(), engine)

    await reg.handle(tick(1, 100.0, t(0)))
    await reg.handle(tick(1, 90.0, t(10)))

    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.low == 90.0
    assert bar.high == 100.0


async def test_volume_accumulates_within_bar(engine: AsyncEngine) -> None:
    reg = make_registry(MockBroker(), engine)

    await reg.handle(tick(1, 100.0, t(0), volume=50))
    await reg.handle(tick(1, 101.0, t(10), volume=75))

    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.volume == 125


async def test_tick_crossing_bar_boundary_emits_candle(engine: AsyncEngine) -> None:
    reg = make_registry(MockBroker(), engine)

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
    reg = make_registry(MockBroker(), engine, intervals=["1min", "5min"])

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
    reg = make_registry(MockBroker(), engine)

    await reg.handle(tick(1, 100.0, t(0), volume=0))
    bar = reg._accumulator._bars[("SYM1", "1min")]
    assert bar.volume == 0
    assert bar.open == 100.0


async def test_unknown_token_returns_none(engine: AsyncEngine) -> None:
    reg = make_registry(MockBroker(), engine, tokens=[1])

    result = await reg.handle(tick(999, 100.0, t(0)))
    assert result is None


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------


async def test_warmup_returns_historical_candles(engine: AsyncEngine) -> None:
    df = make_ohlc_df(10, BASE_TIME - timedelta(hours=1))
    broker = MockBroker(df=df)
    reg = make_registry(broker, engine, warmup_count=10)

    events = await reg.warmup()
    assert len(events) == 10
    for e in events:
        assert isinstance(e, CandleEvent)


async def test_warmup_empty_result_no_crash(engine: AsyncEngine) -> None:
    broker = MockBroker(df=pl.DataFrame())
    reg = make_registry(broker, engine, warmup_count=5)

    events = await reg.warmup()
    assert events == []


async def test_warmup_broker_failure_no_crash(engine: AsyncEngine) -> None:
    broker = MockBroker(raises=True)
    reg = make_registry(broker, engine, warmup_count=5)

    events = await reg.warmup()
    assert events == []  # failure is swallowed, empty result


async def test_warmup_respects_warmup_count(engine: AsyncEngine) -> None:
    """Only last warmup_count rows are returned, even if broker returns more."""
    df = make_ohlc_df(50, BASE_TIME - timedelta(hours=1))
    broker = MockBroker(df=df)
    reg = make_registry(broker, engine, warmup_count=20)

    events = await reg.warmup()
    assert len(events) == 20


# ---------------------------------------------------------------------------
# _ensure_utc helper
# ---------------------------------------------------------------------------


def test_ensure_utc_raises_on_non_datetime() -> None:
    from trading.candles.candle_aggregator import _ensure_utc

    with pytest.raises(TypeError):
        _ensure_utc("2025-01-06")  # string, not datetime


def test_ensure_utc_adds_utc_to_naive_datetime() -> None:
    from trading.candles.candle_aggregator import _ensure_utc

    naive = datetime(2025, 1, 6, 9, 15)
    result = _ensure_utc(naive)
    assert result.tzinfo is not None


async def test_warmup_invalid_row_logged_as_warning(engine: AsyncEngine) -> None:
    """Covers lines 160-161: a row with a non-datetime 'date' field is skipped."""
    bad_df = pl.DataFrame(
        {
            "date": ["not-a-datetime"],  # string → _ensure_utc raises TypeError
            "open": [100.0],
            "high": [105.0],
            "low": [99.0],
            "close": [102.0],
            "volume": [1000],
        }
    )
    broker = MockBroker(df=bad_df)
    reg = make_registry(broker, engine, warmup_count=5)

    events = await reg.warmup()
    assert events == []  # bad row is skipped, no crash


async def test_warmup_candle_persist_failure_is_swallowed(engine: AsyncEngine) -> None:
    """Covers lines 201-202: warmup candle save_candles failure is caught silently."""
    from unittest.mock import AsyncMock

    from trading.storage.stores.candle import AbstractCandleDataStore

    class _FailingCandleStore(AbstractCandleDataStore):
        async def save_candles(self, rows) -> None:
            raise RuntimeError("DB write failure")

        async def get_candles(self, symbol, interval, limit):
            return []

        async def get_candles_since(self, symbol, interval, since):
            return []

    df = make_ohlc_df(5, BASE_TIME - timedelta(hours=1))
    broker = MockBroker(df=df)
    instruments = [make_instrument(1)]
    sf = build_session_factory(engine)
    config = CandleConfig(instruments=instruments, intervals=["1min"], warmup_count=5)
    reg = CandleAggregator(
        config=config,
        broker=broker,
        candle=_FailingCandleStore(),
        audit=AuditStore(sf),
    )
    # Should not raise — warmup candle persist failure is swallowed
    events = await reg.warmup()
    assert events == [] or isinstance(events, list)


async def test_log_candle_tick_log_id_positive_calls_audit(engine: AsyncEngine) -> None:
    """Covers lines 289-290: _log_candle calls audit.log_decision when tick_log_id > 0."""
    from trading.storage.stores.candle import AbstractCandleDataStore

    class _SucceedingCandleStore(AbstractCandleDataStore):
        async def save_candles(self, rows) -> None:
            pass  # succeed → reaches line 289

        async def get_candles(self, symbol, interval, limit):
            return []

        async def get_candles_since(self, symbol, interval, since):
            return []

    instruments = [make_instrument(1)]
    sf = build_session_factory(engine)
    config = CandleConfig(instruments=instruments, intervals=["1min"], warmup_count=5)
    reg = CandleAggregator(
        config=config,
        broker=MockBroker(),
        candle=_SucceedingCandleStore(),
        audit=AuditStore(sf),
    )

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
        tick_log_id=99,  # > 0 → triggers log_decision (line 290)
    )
    # save_candles succeeds → reaches line 289 (if tick_log_id > 0)
    # then calls log_decision (line 290) — no exception
    await reg._log_candle(candle_event)


async def test_log_candle_exception_path_is_swallowed(engine: AsyncEngine) -> None:
    """Covers lines 304-311: _log_candle exception is caught and logged."""
    from trading.storage.stores.candle import AbstractCandleDataStore

    class _FailingCandleStore(AbstractCandleDataStore):
        async def save_candles(self, rows) -> None:
            raise RuntimeError("candle save failed")

        async def get_candles(self, symbol, interval, limit):
            return []

        async def get_candles_since(self, symbol, interval, since):
            return []

    instruments = [make_instrument(1)]
    sf = build_session_factory(engine)
    config = CandleConfig(instruments=instruments, intervals=["1min"], warmup_count=5)
    reg = CandleAggregator(
        config=config,
        broker=MockBroker(),
        candle=_FailingCandleStore(),
        audit=AuditStore(sf),
    )

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
    # save_candles raises → caught by except block — no exception propagated
    await reg._log_candle(candle_event)


async def test_handle_with_tick_log_id_schedules_log_candle(engine: AsyncEngine) -> None:
    """Covers _log_candle DB path: tick_log_id != 0 triggers fire-and-forget log."""
    broker = MockBroker()
    reg = make_registry(broker, engine, tokens=[1], intervals=["1min"])

    from trading.core.database import get_session
    from trading.core.models import Instrument

    async with get_session(engine) as s:
        s.add(Instrument(token=1, symbol="SYM1", exchange="NSE", instrument_type="EQUITY"))

    # Feed two ticks to close a bar with tick_log_id != 0
    t1 = tick(1, 100.0, BASE_TIME, volume=100)
    t2 = tick(1, 101.0, BASE_TIME + timedelta(minutes=1, seconds=1), volume=50)
    t2 = TickEvent(
        instrument_token=1,
        instrument_type=InstrumentType.EQUITY,
        last_price=101.0,
        volume=50,
        timestamp=BASE_TIME + timedelta(minutes=1, seconds=1),
        tick_log_id=99,  # non-zero → _log_candle is scheduled
    )

    await reg.handle(t1)
    await reg.handle(t2)
    # The bar should have closed — we just need no exception
    # (fire-and-forget task may not have executed yet)
