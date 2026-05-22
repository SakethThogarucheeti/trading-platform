"""
Order lifecycle integration tests.

Tests the full path: ValidatedOrderEvent → OrderExecutor → broker → fill → position update in DB.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from trading.broker.paper_broker import PaperBroker, PriceStore
from trading.core.models import Order, Position
from trading.core.schemas import (
    InstrumentType,
    OrderStatus,
    OrderType,
    Side,
    ValidatedOrderEvent,
)
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.storage.stores.trading import TradingStore

sys.path.insert(0, str(Path(__file__).parents[1]))
from helpers import seed_signal


def _validated_order(
    symbol: str = "INFY",
    side: Side = Side.BUY,
    qty: int = 10,
    signal_id: uuid.UUID | None = None,
) -> ValidatedOrderEvent:
    return ValidatedOrderEvent(
        signal_id=signal_id or uuid.uuid4(),
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
        side=side,
        quantity=qty,
        order_type=OrderType.MARKET,
        tick_log_id=0,
    )


class _NullRealBroker:
    """Minimal real broker stub for PaperBroker delegation."""

    async def place_order(self, symbol, side, qty, order_type, limit_price=None):
        return f"ZERODHA_{uuid.uuid4().hex[:8]}"


async def test_place_and_fill(engine, session_factory):
    """Full happy-path: place order → immediate fill via PaperBroker → position updated."""
    price_store = PriceStore()
    price_store.update("INFY", 1500.0)

    trading = TradingStore(session_factory)
    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="paper"),
        broker=PaperBroker(_NullRealBroker()),
        session_factory=session_factory,
        trading=trading,
        price_store=price_store,
    )

    event = _validated_order()
    await seed_signal(session_factory, event)
    await exec_reg.handle(event)

    async with session_factory() as session:
        result = await session.execute(select(Order).where(Order.signal_id == event.signal_id))
        order = result.scalar_one_or_none()

    assert order is not None
    assert order.status == OrderStatus.FILLED.value
    assert order.qty == 10
    assert float(order.avg_price) == pytest.approx(1500.0)


async def test_position_updated_after_fill(engine, session_factory):
    """After a paper fill, the position table must reflect the new holding."""
    price_store = PriceStore()
    price_store.update("INFY", 1500.0)

    trading = TradingStore(session_factory)
    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="paper"),
        broker=PaperBroker(_NullRealBroker()),
        session_factory=session_factory,
        trading=trading,
        price_store=price_store,
    )

    ev = _validated_order(symbol="INFY", side=Side.BUY, qty=10)
    await seed_signal(session_factory, ev)
    await exec_reg.handle(ev)

    async with session_factory() as session:
        result = await session.execute(
            select(Position).where(
                Position.symbol == "INFY",
                Position.instrument_type == InstrumentType.EQUITY.value,
            )
        )
        pos = result.scalar_one_or_none()

    assert pos is not None
    assert pos.net_qty == 10


async def test_idempotency_duplicate_signal(engine, session_factory):
    """Duplicate signal_id must not place a second order."""
    broker_calls: list[str] = []

    class _CountingBroker:
        async def place_order(self, symbol, side, qty, order_type, limit_price=None):
            oid = f"ORDER_{uuid.uuid4().hex[:8]}"
            broker_calls.append(oid)
            return oid

    trading = TradingStore(session_factory)
    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="direct"),
        broker=_CountingBroker(),
        session_factory=session_factory,
        trading=trading,
    )

    event = _validated_order()
    await seed_signal(session_factory, event)
    await exec_reg.handle(event)
    count_after_first = len(broker_calls)

    await exec_reg.handle(event)  # duplicate

    assert len(broker_calls) == count_after_first, (
        "Second call with same signal_id must not reach broker"
    )


async def test_broker_rejection_marks_order_rejected(engine, session_factory):
    """When the broker raises, the order must be marked REJECTED in DB."""

    class _FailingBroker:
        async def place_order(self, *a, **kw):
            raise RuntimeError("Broker unavailable")

    trading = TradingStore(session_factory)
    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="direct"),
        broker=_FailingBroker(),
        session_factory=session_factory,
        trading=trading,
    )

    event = _validated_order()
    await seed_signal(session_factory, event)
    await exec_reg.handle(event)

    async with session_factory() as session:
        result = await session.execute(select(Order).where(Order.signal_id == event.signal_id))
        order = result.scalar_one_or_none()

    assert order is not None
    assert order.status == OrderStatus.REJECTED.value, (
        f"Order must be REJECTED after broker error, got {order.status}"
    )
