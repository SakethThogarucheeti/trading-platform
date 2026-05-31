"""Unit tests for HistoricalDataService — DB-hit, broker-fallback, and helper functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.broker.base.broker import Broker
from trading.candles.historical_data_service import (
    HistoricalDataService,
    _df_to_candle_rows,
    _has_full_coverage,
    _rows_to_df,
    warmup_start,
)
from trading.core.database import build_session_factory, init_db
from trading.storage.stores.candle import AbstractCandleDataStore, CandleDataStore
from quantindicators.types import CandleRow

BASE_TIME = datetime(2025, 1, 6, 9, 15, 0, tzinfo=UTC)
START = BASE_TIME
END = BASE_TIME + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockBroker(Broker):
    def __init__(self, df: pl.DataFrame | None = None, raises: bool = False) -> None:
        self._df = df if df is not None else pl.DataFrame()
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    def get_instruments(self) -> pl.DataFrame:
        return pl.DataFrame()

    async def place_order(self, symbol, side, qty, order_type, limit_price=None, instrument_type="EQUITY", tick_log_id=0) -> str:  # type: ignore[override]
        return "MOCK_ORDER"

    def get_ohlc(self, symbol: str, interval: str, start: datetime, end: datetime) -> pl.DataFrame:
        self.calls.append((symbol, interval))
        if self._raises:
            raise RuntimeError("broker unavailable")
        return self._df


class _InMemoryCandleStore(AbstractCandleDataStore):
    """In-memory implementation for tests — no DB needed."""

    def __init__(self, rows: list[CandleRow] | None = None) -> None:
        self._rows: list[CandleRow] = rows or []

    async def save_candles(self, rows: list[CandleRow]) -> None:
        existing_keys = {(r["symbol"], r["interval"], r["ts"]) for r in self._rows}
        for r in rows:
            if (r["symbol"], r["interval"], r["ts"]) not in existing_keys:
                self._rows.append(r)

    async def get_candles(self, symbol: str, interval: str, limit: int) -> list[CandleRow]:
        matching = [r for r in self._rows if r["symbol"] == symbol and r["interval"] == interval]
        return sorted(matching, key=lambda r: r["ts"])[-limit:]

    async def get_candles_since(self, symbol: str, interval: str, since: datetime) -> list[CandleRow]:
        return sorted(
            [r for r in self._rows if r["symbol"] == symbol and r["interval"] == interval and r["ts"] >= since],
            key=lambda r: r["ts"],
        )


def _make_ohlc_df(n: int, start: datetime, interval_mins: int = 1) -> pl.DataFrame:
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=i * interval_mins)
        price = 100.0 + i
        rows.append({"date": ts, "open": price, "high": price + 1, "low": price - 1, "close": price + 0.5, "volume": 1000 + i})
    return pl.DataFrame(rows)


def _make_candle_rows(n: int, start: datetime, symbol: str = "INFY", interval: str = "1min") -> list[CandleRow]:
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=i)
        price = 100.0 + i
        rows.append(CandleRow(symbol=symbol, interval=interval, ts=ts, open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=1000 + i))
    return rows


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# HistoricalDataService.fetch() — DB-hit path
# ---------------------------------------------------------------------------


async def test_db_hit_returns_cached_rows() -> None:
    """When DB has full coverage, broker is NOT called."""
    rows = _make_candle_rows(60, START, "INFY", "1min")
    store = _InMemoryCandleStore(rows)
    broker = _MockBroker()

    service = HistoricalDataService(broker=broker, candle_store=store)
    result = await service.fetch("INFY", "1min", START, END)

    assert result.fetched_from_broker is False
    assert len(broker.calls) == 0
    assert not result.df.is_empty()


async def test_broker_fallback_when_db_empty() -> None:
    """Empty DB triggers broker call; rows are persisted afterwards."""
    df = _make_ohlc_df(60, START)
    broker = _MockBroker(df=df)
    store = _InMemoryCandleStore()

    service = HistoricalDataService(broker=broker, candle_store=store)
    result = await service.fetch("INFY", "1min", START, END)

    assert result.fetched_from_broker is True
    assert len(broker.calls) == 1
    assert len(result.df) == 60
    # rows should now be persisted
    persisted = await store.get_candles_since("INFY", "1min", START)
    assert len(persisted) == 60


async def test_second_fetch_uses_db() -> None:
    """Second fetch for the same range must not hit the broker again."""
    df = _make_ohlc_df(60, START)
    broker = _MockBroker(df=df)
    store = _InMemoryCandleStore()

    service = HistoricalDataService(broker=broker, candle_store=store)
    await service.fetch("INFY", "1min", START, END)         # broker called
    result2 = await service.fetch("INFY", "1min", START, END)  # DB should serve

    assert result2.fetched_from_broker is False
    assert len(broker.calls) == 1  # still only one broker call total


async def test_broker_error_returns_empty_df() -> None:
    """Broker exception is swallowed; empty DataFrame is returned."""
    store = _InMemoryCandleStore()
    service = HistoricalDataService(broker=_MockBroker(raises=True), candle_store=store)

    result = await service.fetch("INFY", "1min", START, END)

    assert result.fetched_from_broker is True
    assert result.df.is_empty()


async def test_persist_failure_is_swallowed() -> None:
    """If saving to DB fails, fetch still returns the broker DataFrame."""

    class _FailStore(_InMemoryCandleStore):
        async def save_candles(self, rows: list[CandleRow]) -> None:
            raise RuntimeError("DB write failure")

    df = _make_ohlc_df(5, START)
    service = HistoricalDataService(broker=_MockBroker(df=df), candle_store=_FailStore())

    result = await service.fetch("INFY", "1min", START, END)
    assert not result.df.is_empty()


# ---------------------------------------------------------------------------
# _has_full_coverage helper
# ---------------------------------------------------------------------------


def test_has_full_coverage_empty_returns_false() -> None:
    assert _has_full_coverage([], START, END, interval_minutes=1) is False


def test_has_full_coverage_exact_boundaries() -> None:
    rows = _make_candle_rows(61, START)
    assert _has_full_coverage(rows, START, END, interval_minutes=1) is True


def test_has_full_coverage_partial_returns_false() -> None:
    rows = _make_candle_rows(10, START)  # only 10 min, need 60+
    assert _has_full_coverage(rows, START, END, interval_minutes=1) is False


def test_has_full_coverage_tolerates_one_interval_gap() -> None:
    # first row is 1 min after start (within tolerance)
    rows = _make_candle_rows(60, START + timedelta(minutes=1))
    assert _has_full_coverage(rows, START, END, interval_minutes=1) is True


# ---------------------------------------------------------------------------
# warmup_start helper
# ---------------------------------------------------------------------------


def test_warmup_start_returns_past_datetime() -> None:
    now = datetime(2025, 1, 6, 10, 0, tzinfo=UTC)
    result = warmup_start(now, ["1min"], warmup_count=200)
    assert result < now


def test_warmup_start_further_back_for_longer_intervals() -> None:
    now = datetime(2025, 1, 6, 10, 0, tzinfo=UTC)
    start_1min = warmup_start(now, ["1min"], warmup_count=200)
    start_15min = warmup_start(now, ["15min"], warmup_count=200)
    assert start_15min < start_1min


# ---------------------------------------------------------------------------
# _rows_to_df / _df_to_candle_rows round-trip
# ---------------------------------------------------------------------------


def test_rows_to_df_empty() -> None:
    df = _rows_to_df([])
    assert df.is_empty()
    assert set(df.columns) == {"date", "open", "high", "low", "close", "volume"}


def test_round_trip_df_to_rows_to_df() -> None:
    original = _make_ohlc_df(5, START)
    rows = _df_to_candle_rows("INFY", "1min", original)
    assert len(rows) == 5
    assert all(r["symbol"] == "INFY" for r in rows)
    assert all(r["interval"] == "1min" for r in rows)

    rebuilt = _rows_to_df(rows)
    assert len(rebuilt) == 5
    assert list(rebuilt["close"]) == pytest.approx(list(original["close"]))
