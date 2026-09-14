"""Tests for execution/service/order_reconciler.py — OrderReconciler (trading-platform#31)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import build_session_factory, get_session, init_db
from trading.core.clock import SYSTEM_CLOCK
from trading.core.schemas import OrderStatus, Side
from trading.execution.service.executor import ExecConfig, OrderExecutor
from trading.execution.service.fill_handler import FillHandler
from trading.execution.service.order_reconciler import OrderReconciler
from trading.execution.service.position_accountant import PositionAccountant
from trading.execution.storage.models import Order
from trading.execution.storage.store import PositionStore, TradingStore
from trading.storage.cache import CacherFactory, ValueCache
from trading.strategy.storage.models import Signal

NOW = datetime.now(UTC)


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


async def _insert_order(
    session,
    *,
    status: OrderStatus,
    kite_order_id: str,
    client_tag: str | None,
    symbol: str = "INFY",
    side: Side = Side.BUY,
) -> Order:
    signal_id = uuid4()
    session.add(
        Signal(
            id=signal_id,
            strategy_id="test",
            algo_name="algo_a",
            symbol=symbol,
            instrument_type="EQUITY",
            side=side.value,
            signal_type="ENTRY",
            stop_distance=Decimal("10"),
            created_at=NOW,
        )
    )
    order = Order(
        id=uuid4(),
        kite_order_id=kite_order_id,
        signal_id=signal_id,
        status=status.value,
        qty=10,
        avg_price=Decimal("0"),
        created_at=NOW,
        client_tag=client_tag,
    )
    session.add(order)
    return order


def make_kite_client(orders: list[dict]) -> MagicMock:
    client = MagicMock()
    client.orders.return_value = orders
    return client


async def test_reconcile_once_noop_when_nothing_unresolved(engine: AsyncEngine) -> None:
    store = TradingStore(build_session_factory(engine))
    kite_client = make_kite_client([])
    executor = MagicMock()
    executor.handle_fill = AsyncMock()

    reconciler = OrderReconciler(store, kite_client, executor)
    await reconciler.reconcile_once()

    kite_client.orders.assert_not_called()
    executor.handle_fill.assert_not_called()


async def test_reconcile_once_matches_complete_order_and_calls_handle_fill(
    engine: AsyncEngine,
) -> None:
    async with get_session(engine) as s:
        await _insert_order(
            s, status=OrderStatus.PENDING, kite_order_id="PENDING_1", client_tag="tag1"
        )

    store = TradingStore(build_session_factory(engine))
    kite_client = make_kite_client(
        [
            {
                "order_id": "KITE_REAL_1",
                "tag": "tag1",
                "status": "COMPLETE",
                "average_price": 1500.0,
                "filled_quantity": 10,
            }
        ]
    )
    executor = MagicMock()
    executor.handle_fill = AsyncMock()

    reconciler = OrderReconciler(store, kite_client, executor)
    await reconciler.reconcile_once()

    executor.handle_fill.assert_called_once()
    _, kwargs = executor.handle_fill.call_args
    assert kwargs["kite_order_id"] == "KITE_REAL_1"
    assert kwargs["avg_price"] == 1500.0
    assert kwargs["filled_qty"] == 10
    assert kwargs["symbol"] == "INFY"
    assert kwargs["instrument_type"] == "EQUITY"
    assert kwargs["side"] == "BUY"

    from sqlalchemy import select

    async with get_session(engine) as s:
        result = await s.execute(select(Order).where(Order.client_tag == "tag1"))
        row = result.scalar_one()
    assert row.kite_order_id == "KITE_REAL_1"


async def test_reconcile_once_matches_rejected_order_and_marks_terminal(
    engine: AsyncEngine,
) -> None:
    async with get_session(engine) as s:
        order = await _insert_order(
            s, status=OrderStatus.REJECTED, kite_order_id="FAILED_x", client_tag="tag2"
        )
        order.kite_order_id = f"FAILED_{order.id}"

    store = TradingStore(build_session_factory(engine))
    kite_client = make_kite_client(
        [{"order_id": "KITE_REAL_2", "tag": "tag2", "status": "REJECTED"}]
    )
    executor = MagicMock()
    executor.handle_fill = AsyncMock()

    reconciler = OrderReconciler(store, kite_client, executor)
    await reconciler.reconcile_once()

    executor.handle_fill.assert_not_called()

    from sqlalchemy import select

    async with get_session(engine) as s:
        result = await s.execute(select(Order).where(Order.client_tag == "tag2"))
        row = result.scalar_one()
    assert row.status == OrderStatus.REJECTED.value
    assert row.kite_order_id == "KITE_REAL_2"


async def test_reconcile_once_matches_cancelled_order(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        await _insert_order(
            s, status=OrderStatus.PENDING, kite_order_id="PENDING_3", client_tag="tag3"
        )

    store = TradingStore(build_session_factory(engine))
    kite_client = make_kite_client(
        [{"order_id": "KITE_REAL_3", "tag": "tag3", "status": "CANCELLED"}]
    )
    executor = MagicMock()
    executor.handle_fill = AsyncMock()

    reconciler = OrderReconciler(store, kite_client, executor)
    await reconciler.reconcile_once()

    from sqlalchemy import select

    async with get_session(engine) as s:
        result = await s.execute(select(Order).where(Order.client_tag == "tag3"))
        row = result.scalar_one()
    assert row.status == OrderStatus.CANCELLED.value


async def test_reconcile_once_leaves_unmatched_order_alone(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        await _insert_order(
            s, status=OrderStatus.PENDING, kite_order_id="PENDING_4", client_tag="tag4"
        )

    store = TradingStore(build_session_factory(engine))
    kite_client = make_kite_client([])  # not (yet) in the broker's order book
    executor = MagicMock()
    executor.handle_fill = AsyncMock()

    reconciler = OrderReconciler(store, kite_client, executor)
    await reconciler.reconcile_once()

    executor.handle_fill.assert_not_called()
    unresolved = await store.get_unresolved_tagged_orders()
    assert len(unresolved) == 1
    assert unresolved[0][0].client_tag == "tag4"
    assert unresolved[0][0].kite_order_id == "PENDING_4"


async def test_reconcile_once_still_open_at_broker_corrects_id_but_no_terminal(
    engine: AsyncEngine,
) -> None:
    async with get_session(engine) as s:
        await _insert_order(
            s, status=OrderStatus.PENDING, kite_order_id="PENDING_5", client_tag="tag5"
        )

    store = TradingStore(build_session_factory(engine))
    kite_client = make_kite_client(
        [{"order_id": "KITE_REAL_5", "tag": "tag5", "status": "OPEN"}]
    )
    executor = MagicMock()
    executor.handle_fill = AsyncMock()

    reconciler = OrderReconciler(store, kite_client, executor)
    await reconciler.reconcile_once()

    executor.handle_fill.assert_not_called()

    from sqlalchemy import select

    async with get_session(engine) as s:
        result = await s.execute(select(Order).where(Order.client_tag == "tag5"))
        row = result.scalar_one()
    # kite_order_id corrected even though there's no terminal outcome yet --
    # status stays PENDING (still open at the broker, matches the design's
    # documented "leave alone for next poll" behavior).
    assert row.kite_order_id == "KITE_REAL_5"
    assert row.status == OrderStatus.PENDING.value


async def test_reconcile_once_is_idempotent_across_repeated_polls(engine: AsyncEngine) -> None:
    """
    Once a fill is actually applied (real OrderExecutor.handle_fill, not mocked),
    the row's status moves to FILLED and it naturally drops out of
    get_unresolved_tagged_orders() -- a second poll with the same broker
    response must not re-process it.
    """
    async with get_session(engine) as s:
        await _insert_order(
            s, status=OrderStatus.PENDING, kite_order_id="PENDING_7", client_tag="tag7"
        )

    sf = build_session_factory(engine)
    store = TradingStore(sf)
    cacher_factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)
    accountant = PositionAccountant(PositionStore(sf), store, cacher_factory)
    fill_handler = FillHandler(store, accountant)
    executor = OrderExecutor(
        config=ExecConfig(),
        broker=MagicMock(),
        session_factory=sf,
        trading=store,
        fill_handler=fill_handler,
    )

    kite_client = make_kite_client(
        [
            {
                "order_id": "KITE_REAL_7",
                "tag": "tag7",
                "status": "COMPLETE",
                "average_price": 1500.0,
                "filled_quantity": 10,
            }
        ]
    )
    reconciler = OrderReconciler(store, kite_client, executor)

    await reconciler.reconcile_once()
    assert len(await store.get_unresolved_tagged_orders()) == 0

    # Second poll, same broker response -- nothing left to match, no double-fill.
    await reconciler.reconcile_once()

    from sqlalchemy import select

    async with get_session(engine) as s:
        result = await s.execute(select(Order).where(Order.client_tag == "tag7"))
        row = result.scalar_one()
    assert row.status == OrderStatus.FILLED.value
    assert row.kite_order_id == "KITE_REAL_7"


async def test_reconcile_once_ignores_kite_orders_with_no_tag(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        await _insert_order(
            s, status=OrderStatus.PENDING, kite_order_id="PENDING_6", client_tag="tag6"
        )

    store = TradingStore(build_session_factory(engine))
    kite_client = make_kite_client(
        [
            {"order_id": "SOME_OTHER_ORDER", "status": "COMPLETE"},  # no tag key at all
            {"order_id": "ANOTHER", "tag": None, "status": "COMPLETE"},
        ]
    )
    executor = MagicMock()
    executor.handle_fill = AsyncMock()

    reconciler = OrderReconciler(store, kite_client, executor)
    await reconciler.reconcile_once()

    executor.handle_fill.assert_not_called()
    unresolved = await store.get_unresolved_tagged_orders()
    assert len(unresolved) == 1
