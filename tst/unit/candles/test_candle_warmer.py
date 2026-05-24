"""Tests for CandleWarmer — historical candle fetch, parse, and persist."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.broker.base.broker import Broker
from trading.candles.bar_accumulator import SymbolConfig
from trading.candles.candle_warmer import CandleWarmer
from trading.core.database import build_session_factory, init_db
from trading.core.models import Instrument
from trading.core.schemas import CandleEvent, InstrumentType
from trading.storage.stores.candle import CandleDataStore

BASE_TIME = datetime(2025, 1, 6, 9, 15, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
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


def make_symbol(token: int = 1, symbol: str = "SYM1") -> SymbolConfig:
    return SymbolConfig(
        symbol=symbol,
        instrument_token=token,
        instrument_type=InstrumentType.EQUITY,
    )


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def make_warmer(
    broker: Broker,
    engine: AsyncEngine,
    warmup_count: int = 5,
    intervals: list[str] | None = None,
) -> CandleWarmer:
    sf = build_session_factory(engine)
    return CandleWarmer(
        symbols=[make_symbol()],
        intervals=intervals or ["1min"],
        warmup_count=warmup_count,
        broker=broker,
        candle_store=CandleDataStore(sf),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_warmup_returns_historical_candles(engine: AsyncEngine) -> None:
    df = make_ohlc_df(10, BASE_TIME - timedelta(hours=1))
    warmer = make_warmer(MockBroker(df=df), engine, warmup_count=10)

    result = await warmer.fetch()
    assert len(result.candles) == 10
    for e in result.candles:
        assert isinstance(e, CandleEvent)


async def test_warmup_empty_result_no_crash(engine: AsyncEngine) -> None:
    warmer = make_warmer(MockBroker(df=pl.DataFrame()), engine, warmup_count=5)

    result = await warmer.fetch()
    assert result.candles == []
    assert result.fetch_failures == 0


async def test_warmup_broker_failure_no_crash(engine: AsyncEngine) -> None:
    warmer = make_warmer(MockBroker(raises=True), engine, warmup_count=5)

    result = await warmer.fetch()
    assert result.candles == []
    assert result.fetch_failures == 1


async def test_warmup_respects_warmup_count(engine: AsyncEngine) -> None:
    """Only last warmup_count rows are returned, even if broker returns more."""
    df = make_ohlc_df(50, BASE_TIME - timedelta(hours=1))
    warmer = make_warmer(MockBroker(df=df), engine, warmup_count=20)

    result = await warmer.fetch()
    assert len(result.candles) == 20


async def test_warmup_invalid_row_logged_as_warning(engine: AsyncEngine) -> None:
    """A row with a non-datetime 'date' field is skipped, parse_failures incremented."""
    bad_df = pl.DataFrame(
        {
            "date": ["not-a-datetime"],
            "open": [100.0],
            "high": [105.0],
            "low": [99.0],
            "close": [102.0],
            "volume": [1000],
        }
    )
    warmer = make_warmer(MockBroker(df=bad_df), engine, warmup_count=5)

    result = await warmer.fetch()
    assert result.candles == []
    assert result.parse_failures == 1


async def test_warmup_candle_persist_failure_is_swallowed(engine: AsyncEngine) -> None:
    """save_candles failure is swallowed; persist_failures is incremented."""
    from trading.storage.stores.candle import AbstractCandleDataStore

    class _FailingCandleStore(AbstractCandleDataStore):
        async def save_candles(self, rows) -> None:
            raise RuntimeError("DB write failure")

        async def get_candles(self, symbol, interval, limit):
            return []

        async def get_candles_since(self, symbol, interval, since):
            return []

    df = make_ohlc_df(5, BASE_TIME - timedelta(hours=1))
    warmer = CandleWarmer(
        symbols=[make_symbol()],
        intervals=["1min"],
        warmup_count=5,
        broker=MockBroker(df=df),
        candle_store=_FailingCandleStore(),
    )

    result = await warmer.fetch()
    assert result.persist_failures == 1
    assert len(result.candles) == 5  # events still produced despite persist failure


async def test_warmup_result_reports_failure_counts(engine: AsyncEngine) -> None:
    """WarmupResult namedtuple exposes failure counts alongside candles."""
    warmer = make_warmer(MockBroker(raises=True), engine, warmup_count=5)
    result = await warmer.fetch()

    assert result.fetch_failures == 1
    assert result.parse_failures == 0
    assert result.persist_failures == 0
    assert result.candles == []


def test_ensure_utc_raises_on_non_datetime() -> None:
    from trading.candles.candle_warmer import _ensure_utc

    with pytest.raises(TypeError):
        _ensure_utc("2025-01-06")


def test_ensure_utc_adds_utc_to_naive_datetime() -> None:
    from trading.candles.candle_warmer import _ensure_utc

    naive = datetime(2025, 1, 6, 9, 15)
    result = _ensure_utc(naive)
    assert result.tzinfo is not None
