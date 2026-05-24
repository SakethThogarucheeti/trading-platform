"""Integration tests for execution/order_executor.py — handle_fill() position update."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.broker.base.broker import Broker
from trading.core.database import build_session_factory, get_session, init_db
from trading.core.models import Order, Signal
from trading.core.schemas import (
    InstrumentType,
    OrderStatus,
    OrderType,
    Side,
    SignalType,
    ValidatedOrderEvent,
)
from trading.execution.fill_handler import FillHandler
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.execution.position_accountant import PositionAccountant
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.storage.stores.position import PositionStore
from trading.storage.stores.trading import TradingStore

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


class _FakeBroker(Broker):
    def __init__(self, order_id: str = "K001") -> None:
        self._order_id = order_id

    def get_instruments(self):
        import polars as pl
        return pl.DataFrame()

    def get_ohlc(self, symbol, interval, start, end):
        import polars as pl
        return pl.DataFrame()

    async def place_order(self, symbol, side, qty, order_type, limit_price=None, instrument_type="EQUITY", tick_log_id=0) -> str:
        return self._order_id


def _make_factory() -> CacherFactory:
    setup_cache(None)
    return CacherFactory(ValueCache())


def _make_executor(engine: AsyncEngine, order_id: str = "K001") -> OrderExecutor:
    sf = build_session_factory(engine)
    accountant = PositionAccountant(PositionStore(sf), _make_factory())
    fill_handler = FillHandler(TradingStore(sf), accountant)
    return OrderExecutor(
        config=ExecConfig(),
        broker=_FakeBroker(order_id=order_id),
        session_factory=sf,
        trading=TradingStore(sf),
        fill_handler=fill_handler,
    )


async def _insert_order(engine: AsyncEngine, kite_order_id: str) -> None:
    """Insert a signal + placed order so handle_fill can find it."""
    sig_id = uuid4()
    async with get_session(engine) as s:
        s.add(Signal(
            id=sig_id,
            strategy_id="test",
            symbol="INFY",
            instrument_type="EQUITY",
            side="BUY",
            signal_type="ENTRY",
            stop_distance=Decimal("10"),
            created_at=NOW,
        ))
    async with get_session(engine) as s:
        s.add(Order(
            id=uuid4(),
            kite_order_id=kite_order_id,
            signal_id=sig_id,
            status=OrderStatus.PLACED.value,
            qty=10,
            avg_price=Decimal("0"),
            created_at=NOW,
        ))


# ---------------------------------------------------------------------------
# handle_fill() — position updated on fill
# ---------------------------------------------------------------------------


async def test_handle_fill_creates_position(engine: AsyncEngine) -> None:
    """handle_fill() updates order to FILLED and creates a Position row."""
    executor = _make_executor(engine, order_id="K_FILL_001")
    await _insert_order(engine, "K_FILL_001")

    await executor.handle_fill(
        kite_order_id="K_FILL_001",
        avg_price=1500.0,
        filled_qty=10,
        symbol="INFY",
        instrument_type="EQUITY",
        side="BUY",
    )

    sf = build_session_factory(engine)
    pos = await PositionStore(sf).get_position("INFY", "EQUITY")
    assert pos is not None
    assert pos.net_qty == 10
    assert float(pos.avg_price) == pytest.approx(1500.0)


async def test_handle_fill_unknown_order_no_position(engine: AsyncEngine) -> None:
    """handle_fill() for an unknown order returns early without creating a position."""
    executor = _make_executor(engine)

    await executor.handle_fill(
        kite_order_id="GHOST_ORDER",
        avg_price=1500.0,
        filled_qty=10,
        symbol="INFY",
        instrument_type="EQUITY",
        side="BUY",
    )

    sf = build_session_factory(engine)
    pos = await PositionStore(sf).get_position("INFY", "EQUITY")
    assert pos is None
