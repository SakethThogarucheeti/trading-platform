"""Tests for execution/order_executor.py — OrderExecutor, and execution/idempotency.py"""

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
    ValidatedOrderEvent,
)
from trading.execution.idempotency import is_duplicate
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.storage.stores.trading import TradingStore

NOW = datetime.now(UTC)

# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------


class MockBroker(Broker):
    def __init__(self, order_id: str = "KITE_001", raises: bool = False) -> None:
        self._order_id = order_id
        self._raises = raises
        self.place_order_calls: list[dict] = []

    def get_instruments(self):  # type: ignore[override]
        import polars as pl

        return pl.DataFrame()

    def get_ohlc(self, symbol, interval, start, end):  # type: ignore[override]
        import polars as pl

        return pl.DataFrame()

    async def place_order(self, symbol, side, qty, order_type, limit_price=None) -> str:  # type: ignore[override]
        self.place_order_calls.append(dict(symbol=symbol, side=side, qty=qty))
        if self._raises:
            raise RuntimeError("broker error")
        return self._order_id


# ---------------------------------------------------------------------------
# Mock price store (for paper trading)
# ---------------------------------------------------------------------------


class MockPriceStore:
    def __init__(self, prices: dict[str, float] | None = None) -> None:
        self._prices = prices or {}

    def get(self, symbol: str) -> float | None:
        return self._prices.get(symbol)

    def update(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def make_registry(
    engine: AsyncEngine,
    broker: MockBroker | None = None,
    exec_id: str = "direct",
    price_store: MockPriceStore | None = None,
) -> OrderExecutor:
    sf = build_session_factory(engine)
    config = ExecConfig(exec_id=exec_id)
    return OrderExecutor(
        config=config,
        broker=broker or MockBroker(),
        session_factory=sf,
        trading=TradingStore(sf),
        price_store=price_store,
    )


def make_validated(signal_id=None, **overrides) -> ValidatedOrderEvent:
    base = dict(
        signal_id=signal_id or uuid4(),
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        limit_price=None,
        tick_log_id=1,
    )
    return ValidatedOrderEvent(**{**base, **overrides})  # type: ignore[arg-type]


async def _insert_signal(engine: AsyncEngine, sig_id) -> None:
    async with get_session(engine) as s:
        s.add(
            Signal(
                id=sig_id,
                strategy_id="s",
                symbol="INFY",
                instrument_type="EQUITY",
                side="BUY",
                signal_type="ENTRY",
                stop_distance=Decimal("10"),
                created_at=NOW,
            )
        )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_is_duplicate_returns_false_for_new_signal(engine: AsyncEngine) -> None:
    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    async with get_session(engine) as s:
        result = await is_duplicate(sig_id, s)
    assert result is False


async def test_is_duplicate_returns_true_when_order_exists(engine: AsyncEngine) -> None:
    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    async with get_session(engine) as s:
        s.add(
            Order(
                id=uuid4(),
                kite_order_id="K99",
                signal_id=sig_id,
                status=OrderStatus.PLACED.value,
                qty=5,
                avg_price=Decimal("0"),
                created_at=NOW,
            )
        )
    async with get_session(engine) as s:
        result = await is_duplicate(sig_id, s)
    assert result is True


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------


async def test_valid_order_calls_broker(engine: AsyncEngine) -> None:
    broker = MockBroker(order_id="KITE_100")
    reg = make_registry(engine, broker)

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    await reg.handle(make_validated(signal_id=sig_id))

    assert len(broker.place_order_calls) == 1
    assert broker.place_order_calls[0]["symbol"] == "INFY"


async def test_valid_order_persisted_as_placed(engine: AsyncEngine) -> None:
    broker = MockBroker(order_id="KITE_200")
    reg = make_registry(engine, broker)

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    await reg.handle(make_validated(signal_id=sig_id))

    async with get_session(engine) as s:
        from sqlalchemy import select

        result = await s.execute(select(Order).where(Order.kite_order_id == "KITE_200"))
        order = result.scalars().first()

    assert order is not None
    assert order.status == OrderStatus.PLACED.value


async def test_duplicate_signal_id_not_re_placed(engine: AsyncEngine) -> None:
    broker = MockBroker()
    reg = make_registry(engine, broker)

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    event = make_validated(signal_id=sig_id)

    await reg.handle(event)
    await reg.handle(event)  # duplicate

    assert len(broker.place_order_calls) == 1


async def test_broker_error_marks_order_rejected(engine: AsyncEngine) -> None:
    broker = MockBroker(raises=True)
    reg = make_registry(engine, broker)

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    await reg.handle(make_validated(signal_id=sig_id))

    async with get_session(engine) as s:
        from sqlalchemy import select

        result = await s.execute(select(Order).where(Order.signal_id == sig_id))
        order = result.scalars().first()

    assert order is not None
    assert order.status == OrderStatus.REJECTED.value


async def test_broker_timeout_marks_order_rejected(engine: AsyncEngine) -> None:
    class _TimeoutBroker(MockBroker):
        async def place_order(self, symbol, side, qty, order_type, limit_price=None) -> str:  # type: ignore[override]
            raise RuntimeError("ZerodhaBroker: place_order timed out after 10.0s")

    reg = make_registry(engine, _TimeoutBroker())

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    await reg.handle(make_validated(signal_id=sig_id))

    async with get_session(engine) as s:
        from sqlalchemy import select

        result = await s.execute(select(Order).where(Order.signal_id == sig_id))
        order = result.scalars().first()

    assert order is not None
    assert order.status == OrderStatus.REJECTED.value


# ---------------------------------------------------------------------------
# Paper trading auto-fill
# ---------------------------------------------------------------------------


async def test_paper_trading_auto_fills_at_price_store_price(engine: AsyncEngine) -> None:
    price_store = MockPriceStore({"INFY": 1500.0})
    broker = MockBroker(order_id="PAPER_001")
    reg = make_registry(engine, broker, exec_id="paper", price_store=price_store)

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    await reg.handle(make_validated(signal_id=sig_id))

    sf = build_session_factory(engine)
    pos = await TradingStore(sf).get_position("INFY", "EQUITY")

    assert pos is not None
    assert pos.net_qty == 10
    assert float(pos.avg_price) == pytest.approx(1500.0)


async def test_paper_trading_no_fill_when_price_unknown(engine: AsyncEngine) -> None:
    price_store = MockPriceStore()  # empty — no price for INFY
    broker = MockBroker(order_id="PAPER_002")
    reg = make_registry(engine, broker, exec_id="paper", price_store=price_store)

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    await reg.handle(make_validated(signal_id=sig_id))

    sf = build_session_factory(engine)
    pos = await TradingStore(sf).get_position("INFY", "EQUITY")

    assert pos is None  # no fill, no position


async def test_direct_exec_does_not_auto_fill(engine: AsyncEngine) -> None:
    """exec_id=direct with a price_store — price_store should be ignored."""
    price_store = MockPriceStore({"INFY": 1500.0})
    broker = MockBroker(order_id="DIRECT_001")
    # exec_id="direct" means price_store is not used
    reg = make_registry(engine, broker, exec_id="direct", price_store=price_store)

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    await reg.handle(make_validated(signal_id=sig_id))

    sf = build_session_factory(engine)
    pos = await TradingStore(sf).get_position("INFY", "EQUITY")

    assert pos is None


async def test_handle_fill_unknown_order_returns_early(engine: AsyncEngine) -> None:
    """handle_fill for an unknown kite_order_id hits the NotFoundError path (lines 143-145)."""
    broker = MockBroker()
    reg = make_registry(engine, broker)

    # kite_order_id "GHOST" does not exist in the DB → NotFoundError → early return, no crash
    await reg._handle_fill(
        kite_order_id="GHOST",
        avg_price=100.0,
        filled_qty=10,
        symbol="INFY",
        instrument_type="EQUITY",
        side="BUY",
    )
