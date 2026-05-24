"""
Order lifecycle integration tests.

Tests the full path: ValidatedOrderEvent → OrderExecutor → broker → fill → position update in DB.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import httpx
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
from trading.execution.fill_handler import FillHandler
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.execution.position_accountant import PositionAccountant
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.storage.stores.position import PositionStore
from trading.storage.stores.trading import TradingStore

sys.path.insert(0, str(Path(__file__).parents[1]))
from helpers import seed_signal

_POSTBACK_URL = "http://localhost:8081/api/postback"


def _make_factory() -> CacherFactory:
    setup_cache(None)
    return CacherFactory(ValueCache())


def _make_paper_pair(
    session_factory,
    price_store: PriceStore,
) -> tuple[OrderExecutor, PaperBroker]:
    """
    Build a (OrderExecutor, PaperBroker) pair wired via an in-process postback transport.

    The transport calls executor.handle_fill() directly, mirroring the real
    /api/postback endpoint without needing a running HTTP server.
    """
    trading = TradingStore(session_factory)
    factory = _make_factory()
    accountant = PositionAccountant(PositionStore(session_factory), factory)
    fill_handler = FillHandler(trading, accountant)

    # Placeholder broker so OrderExecutor can be constructed; replaced below.
    class _PlaceholderBroker:
        async def place_order(self, *a, **kw) -> str:
            raise RuntimeError("placeholder — should not be called directly")

    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="paper"),
        broker=_PlaceholderBroker(),  # type: ignore[arg-type]
        session_factory=session_factory,
        trading=trading,
        fill_handler=fill_handler,
    )

    class _PostbackTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            if payload.get("status") != "COMPLETE":
                return httpx.Response(200, json={"ok": True, "skipped": True})
            await exec_reg.handle_fill(
                kite_order_id=payload["order_id"],
                avg_price=float(payload["average_price"]),
                filled_qty=int(payload["filled_quantity"]),
                symbol=payload["tradingsymbol"],
                instrument_type=payload.get("instrument_type", "EQUITY"),
                side=payload["transaction_type"],
                tick_log_id=int(payload.get("tick_log_id", 0)),
            )
            return httpx.Response(200, json={"ok": True})

    broker = PaperBroker(
        _NullRealBroker(),
        price_store=price_store,
        postback_url=_POSTBACK_URL,
        http_client=httpx.AsyncClient(transport=_PostbackTransport()),
    )
    exec_reg._broker = broker  # wire real broker now that both objects exist
    return exec_reg, broker


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

    exec_reg, _ = _make_paper_pair(session_factory, price_store)

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

    exec_reg, _ = _make_paper_pair(session_factory, price_store)

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


def _make_direct_executor(session_factory, broker) -> OrderExecutor:
    trading = TradingStore(session_factory)
    accountant = PositionAccountant(PositionStore(session_factory), _make_factory())
    fill_handler = FillHandler(trading, accountant)
    return OrderExecutor(
        config=ExecConfig(exec_id="direct"),
        broker=broker,
        session_factory=session_factory,
        trading=trading,
        fill_handler=fill_handler,
    )


async def test_idempotency_duplicate_signal(engine, session_factory):
    """Duplicate signal_id must not place a second order."""
    broker_calls: list[str] = []

    class _CountingBroker:
        async def place_order(self, symbol, side, qty, order_type, limit_price=None, instrument_type="EQUITY", tick_log_id=0):
            oid = f"ORDER_{uuid.uuid4().hex[:8]}"
            broker_calls.append(oid)
            return oid

    exec_reg = _make_direct_executor(session_factory, _CountingBroker())

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

    exec_reg = _make_direct_executor(session_factory, _FailingBroker())

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
