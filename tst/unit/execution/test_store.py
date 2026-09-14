"""Tests for execution/storage/store.py — PositionStore.get_algo_position (trading-platform#8)"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import build_session_factory, get_session, init_db
from trading.core.schemas import OrderStatus, Side
from trading.execution.storage.models import Order
from trading.execution.storage.store import PositionStore
from trading.strategy.storage.models import Signal

NOW = datetime.now(UTC)


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


async def _fill(
    session, *, algo_name: str, symbol: str, side: Side, qty: int, avg_price: str
) -> None:
    signal_id = uuid4()
    session.add(
        Signal(
            id=signal_id,
            strategy_id="test",
            algo_name=algo_name,
            symbol=symbol,
            instrument_type="EQUITY",
            side=side.value,
            signal_type="ENTRY",
            stop_distance=Decimal("10"),
            created_at=NOW,
        )
    )
    session.add(
        Order(
            id=uuid4(),
            kite_order_id=f"KITE_{uuid4().hex[:8]}",
            signal_id=signal_id,
            status=OrderStatus.FILLED.value,
            qty=qty,
            avg_price=Decimal(avg_price),
            created_at=NOW,
        )
    )


async def test_get_algo_position_returns_none_with_no_fills(engine: AsyncEngine) -> None:
    store = PositionStore(build_session_factory(engine))
    result = await store.get_algo_position("INFY", "EQUITY", "algo_a")
    assert result is None


async def test_get_algo_position_single_fill(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        await _fill(s, algo_name="algo_a", symbol="INFY", side=Side.BUY, qty=10, avg_price="1500")

    store = PositionStore(build_session_factory(engine))
    result = await store.get_algo_position("INFY", "EQUITY", "algo_a")
    assert result is not None
    assert result.net_qty == 10
    assert result.avg_price == Decimal("1500")


async def test_get_algo_position_folds_multiple_fills_via_ledger(engine: AsyncEngine) -> None:
    """Two BUY fills at different prices -> size-weighted average, same as the real ledger."""
    async with get_session(engine) as s:
        await _fill(s, algo_name="algo_a", symbol="INFY", side=Side.BUY, qty=10, avg_price="1500")
        await _fill(s, algo_name="algo_a", symbol="INFY", side=Side.BUY, qty=10, avg_price="1600")

    store = PositionStore(build_session_factory(engine))
    result = await store.get_algo_position("INFY", "EQUITY", "algo_a")
    assert result is not None
    assert result.net_qty == 20
    assert result.avg_price == Decimal("1550")


async def test_get_algo_position_scoped_per_algo_not_blended(engine: AsyncEngine) -> None:
    """Two different algos on the same instrument -> each sees only its own fills."""
    async with get_session(engine) as s:
        await _fill(
            s, algo_name="algo_a", symbol="INFY", side=Side.SELL, qty=34, avg_price="1131.83"
        )
        await _fill(s, algo_name="algo_b", symbol="INFY", side=Side.BUY, qty=5, avg_price="1200")

    store = PositionStore(build_session_factory(engine))

    algo_a_pos = await store.get_algo_position("INFY", "EQUITY", "algo_a")
    assert algo_a_pos is not None
    assert algo_a_pos.net_qty == -34

    algo_b_pos = await store.get_algo_position("INFY", "EQUITY", "algo_b")
    assert algo_b_pos is not None
    assert algo_b_pos.net_qty == 5

    algo_c_pos = await store.get_algo_position("INFY", "EQUITY", "algo_c")
    assert algo_c_pos is None


async def test_get_algo_position_ignores_unfilled_orders(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        signal_id = uuid4()
        s.add(
            Signal(
                id=signal_id,
                strategy_id="test",
                algo_name="algo_a",
                symbol="INFY",
                instrument_type="EQUITY",
                side=Side.BUY.value,
                signal_type="ENTRY",
                stop_distance=Decimal("10"),
                created_at=NOW,
            )
        )
        s.add(
            Order(
                id=uuid4(),
                kite_order_id="KITE_PENDING",
                signal_id=signal_id,
                status=OrderStatus.PENDING.value,
                qty=10,
                avg_price=Decimal("0"),
                created_at=NOW,
            )
        )

    store = PositionStore(build_session_factory(engine))
    result = await store.get_algo_position("INFY", "EQUITY", "algo_a")
    assert result is None
