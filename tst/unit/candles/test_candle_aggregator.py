"""Tests for CandleAggregator lifecycle and warmup replay."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest
from anyio import create_task_group, sleep
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.broker.base.broker import Broker
from trading.core.database import build_session_factory, init_db
from trading.core.models import Instrument
from trading.core.schemas import CandleEvent, InstrumentType
from trading.candles.candle_aggregator import CandleAggregator, CandleAggregatorComponent, CandleConfig
from trading.core.lifecycle.component import ComponentState
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.candle import CandleDataStore


class _StubBroker(Broker):
    def get_instruments(self) -> pl.DataFrame:
        return pl.DataFrame()

    def get_ohlc(self, symbol, interval, start, end) -> pl.DataFrame:  # type: ignore[override]
        return pl.DataFrame()

    async def place_order(self, symbol, side, qty, order_type, limit_price=None) -> str:  # type: ignore[override]
        return "STUB_001"


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# CandleAggregator
# ---------------------------------------------------------------------------


async def test_candle_aggregator_starts_and_reaches_running(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)

    from trading.core.database import get_session

    async with get_session(engine) as s:
        s.add(Instrument(token=1, symbol="INFY", exchange="NSE", instrument_type="EQUITY"))

    config = CandleConfig(
        instruments=[Instrument(token=1, symbol="INFY", exchange="NSE", instrument_type="EQUITY")],
        intervals=["1min"],
        warmup_count=5,
    )
    reg = CandleAggregator(
        config=config,
        broker=_StubBroker(),
        candle=CandleDataStore(sf),
        audit=AuditStore(sf),
    )
    agg = CandleAggregatorComponent(reg)

    async with create_task_group() as tg:
        await tg.start(agg.start)
        await sleep(0.1)
        assert agg.state == ComponentState.RUNNING
        await agg.stop()


async def test_candle_aggregator_add_algo_registry_and_warmup_replay(
    engine: AsyncEngine,
) -> None:
    """add_algo_registry registers a callback and warmup candles are replayed."""
    candle = CandleEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        interval="1min",
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )

    mock_candle_reg = MagicMock()
    mock_candle_reg.warmup = AsyncMock(return_value=[candle])

    mock_algo_reg = MagicMock()
    mock_algo_reg.handle = AsyncMock(return_value=[])

    agg = CandleAggregatorComponent(mock_candle_reg)
    agg.add_algo_registry(mock_algo_reg)

    await agg._setup()

    mock_algo_reg.handle.assert_called_once_with(candle)


async def test_candle_aggregator_warmup_error_does_not_abort(
    engine: AsyncEngine,
) -> None:
    """An exception in a warmup replay call is logged but does not propagate."""
    candle = CandleEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        interval="1min",
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )

    mock_candle_reg = MagicMock()
    mock_candle_reg.warmup = AsyncMock(return_value=[candle])

    mock_algo_reg = MagicMock()
    mock_algo_reg.handle = AsyncMock(side_effect=RuntimeError("boom"))

    agg = CandleAggregatorComponent(mock_candle_reg)
    agg.add_algo_registry(mock_algo_reg)

    await agg._setup()


async def test_candle_aggregator_no_warmup_candles_no_replay(
    engine: AsyncEngine,
) -> None:
    """When warmup() returns an empty list, no algo handles are called."""
    mock_candle_reg = MagicMock()
    mock_candle_reg.warmup = AsyncMock(return_value=[])

    mock_algo_reg = MagicMock()
    mock_algo_reg.handle = AsyncMock(return_value=[])

    agg = CandleAggregatorComponent(mock_candle_reg)
    agg.add_algo_registry(mock_algo_reg)

    await agg._setup()

    mock_algo_reg.handle.assert_not_called()
