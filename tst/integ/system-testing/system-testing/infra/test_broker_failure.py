"""
Broker failure mode tests.

Verifies that OrderExecutor handles:
- Broker timeout (TimeoutError)
- Broker API error (generic exception)
- Consecutive failures without deadlock
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from trading.core.models import Order
from trading.core.schemas import (
    InstrumentType,
    OrderStatus,
    OrderType,
    Side,
    ValidatedOrderEvent,
)
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.storage.repository import Repository

sys.path.insert(0, str(Path(__file__).parents[1]))
from helpers import seed_signal


def _event(symbol: str = "INFY") -> ValidatedOrderEvent:
    return ValidatedOrderEvent(
        signal_id=uuid.uuid4(),
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        quantity=5,
        order_type=OrderType.MARKET,
        tick_log_id=0,
    )


async def test_broker_timeout_produces_rejected_order(engine, session_factory):
    """TimeoutError from broker must mark the order REJECTED in DB."""

    class _TimeoutBroker:
        async def place_order(self, *a, **kw):
            raise TimeoutError("broker timeout")

        def get_instruments(self):
            import polars as pl

            return pl.DataFrame()

        def get_ohlc(self, *a, **kw):
            import polars as pl

            return pl.DataFrame()

    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="direct"),
        broker=_TimeoutBroker(),
        session_factory=session_factory,
        repo=Repository(),
    )

    event = _event()
    await seed_signal(session_factory, event)
    await exec_reg.handle(event)

    async with session_factory() as session:
        result = await session.execute(select(Order).where(Order.signal_id == event.signal_id))
        order = result.scalar_one_or_none()

    assert order is not None
    assert order.status == OrderStatus.REJECTED.value, (
        f"Timeout must produce REJECTED order, got {order.status}"
    )


async def test_consecutive_broker_failures_no_deadlock(engine, session_factory):
    """Multiple consecutive broker failures must not deadlock or hang."""
    call_count = 0

    class _AlwaysFailBroker:
        async def place_order(self, *a, **kw):
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"Failure #{call_count}")

        def get_instruments(self):
            import polars as pl

            return pl.DataFrame()

        def get_ohlc(self, *a, **kw):
            import polars as pl

            return pl.DataFrame()

    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="direct"),
        broker=_AlwaysFailBroker(),
        session_factory=session_factory,
        repo=Repository(),
    )

    for _ in range(5):
        ev = _event()
        await seed_signal(session_factory, ev)
        await exec_reg.handle(ev)

    assert call_count == 5, f"Broker should have been called 5 times, got {call_count}"


async def test_broker_api_error_leaves_db_consistent(engine, session_factory):
    """After a broker API error, the DB row must be REJECTED, never stuck in PENDING."""

    class _ErrorBroker:
        async def place_order(self, *a, **kw):
            raise ValueError("Invalid API key")

        def get_instruments(self):
            import polars as pl

            return pl.DataFrame()

        def get_ohlc(self, *a, **kw):
            import polars as pl

            return pl.DataFrame()

    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="direct"),
        broker=_ErrorBroker(),
        session_factory=session_factory,
        repo=Repository(),
    )

    event = _event()
    await seed_signal(session_factory, event)
    await exec_reg.handle(event)

    async with session_factory() as session:
        result = await session.execute(select(Order).where(Order.signal_id == event.signal_id))
        order = result.scalar_one_or_none()

    assert order is not None
    assert order.status != OrderStatus.PENDING.value, "Order must not be left in PENDING state"
    assert order.status == OrderStatus.REJECTED.value, (
        f"Order must be REJECTED after broker error, got {order.status}"
    )
