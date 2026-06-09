"""
State recovery tests.

Verifies that:
- Duplicate orders are not placed if the process restarts mid-trade.
- Idempotency holds across multiple executions with the same signal_id.
- Orders can be read back from DB after write.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from trading.core.clock import SYSTEM_CLOCK
from trading.core.models import Order
from trading.core.schemas import InstrumentType, OrderType, Side, ValidatedOrderEvent
from trading.execution.service.fill_handler import FillHandler
from trading.execution.service.executor import ExecConfig, OrderExecutor
from trading.execution.service.position_accountant import PositionAccountant
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.execution.storage.store import PositionStore
from trading.execution.storage.store import TradingStore


def _make_fill_handler(session_factory):
    setup_cache(None)
    accountant = PositionAccountant(PositionStore(session_factory), CacherFactory(ValueCache(), SYSTEM_CLOCK))
    return FillHandler(TradingStore(session_factory), accountant)

sys.path.insert(0, str(Path(__file__).parents[1]))
from helpers import seed_signal


def _validated_order(signal_id: uuid.UUID | None = None) -> ValidatedOrderEvent:
    return ValidatedOrderEvent(
        signal_id=signal_id or uuid.uuid4(),
        symbol="RELIANCE",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        quantity=5,
        order_type=OrderType.MARKET,
        tick_log_id=0,
    )


class _CountingBroker:
    def __init__(self) -> None:
        self.call_count = 0

    async def place_order(self, symbol, side, qty, order_type, limit_price=None):
        self.call_count += 1
        return f"ORDER_{uuid.uuid4().hex[:8]}"


async def test_no_duplicate_order_on_restart(engine, session_factory):
    """
    Simulating a 'restart': execute the same signal_id twice.
    The second execution must be silently dropped (idempotency).
    """
    broker = _CountingBroker()
    trading = TradingStore(session_factory)
    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="paper"),
        broker=broker,
        session_factory=session_factory,
        trading=trading,
        fill_handler=_make_fill_handler(session_factory),
    )

    event = _validated_order()
    await seed_signal(session_factory, event)
    await exec_reg.handle(event)
    count_after_first = broker.call_count

    # Simulate restart — same signal_id handled again
    await exec_reg.handle(event)

    assert broker.call_count == count_after_first, (
        f"Broker was called {broker.call_count} times for the same signal_id; "
        f"expected {count_after_first}"
    )


async def test_order_persisted_in_db(engine, session_factory):
    """Placed orders must be readable back from the database."""
    class _SimpleBroker:
        async def place_order(self, *a, **kw):
            return f"ORDER_{uuid.uuid4().hex[:8]}"

    trading = TradingStore(session_factory)
    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="paper"),
        broker=_SimpleBroker(),
        session_factory=session_factory,
        trading=trading,
        fill_handler=_make_fill_handler(session_factory),
    )

    event = _validated_order()
    await seed_signal(session_factory, event)
    await exec_reg.handle(event)

    async with session_factory() as session:
        result = await session.execute(select(Order).where(Order.signal_id == event.signal_id))
        order_row = result.scalar_one_or_none()

    assert order_row is not None, "Order row should exist in database after handle()"
    assert order_row.qty == 5
