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
from trading.execution.fill_handler import FillHandler
from trading.execution.idempotency import is_duplicate
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.execution.position_accountant import PositionAccountant
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.storage.stores.position import PositionStore
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

    async def place_order(self, symbol, side, qty, order_type, limit_price=None, instrument_type="EQUITY", tick_log_id=0) -> str:  # type: ignore[override]
        self.place_order_calls.append(dict(symbol=symbol, side=side, qty=qty))
        if self._raises:
            raise RuntimeError("broker error")
        return self._order_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def _make_factory() -> CacherFactory:
    setup_cache(None)
    return CacherFactory(ValueCache())


def make_registry(
    engine: AsyncEngine,
    broker: MockBroker | None = None,
) -> OrderExecutor:
    sf = build_session_factory(engine)
    accountant = PositionAccountant(PositionStore(sf), _make_factory())
    fill_handler = FillHandler(TradingStore(sf), accountant)
    return OrderExecutor(
        config=ExecConfig(),
        broker=broker or MockBroker(),
        session_factory=sf,
        trading=TradingStore(sf),
        fill_handler=fill_handler,
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
        async def place_order(self, symbol, side, qty, order_type, limit_price=None, instrument_type="EQUITY", tick_log_id=0) -> str:  # type: ignore[override]
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


async def test_handle_fill_unknown_order_returns_early(engine: AsyncEngine) -> None:
    """handle_fill for an unknown kite_order_id hits the NotFoundError path and returns early."""
    broker = MockBroker()
    reg = make_registry(engine, broker)

    # kite_order_id "GHOST" does not exist in the DB → NotFoundError → early return, no crash
    await reg.handle_fill(
        kite_order_id="GHOST",
        avg_price=100.0,
        filled_qty=10,
        symbol="INFY",
        instrument_type="EQUITY",
        side="BUY",
    )


async def test_pnl_updated_in_cache_after_handle_fill(engine: AsyncEngine) -> None:
    """PnL cache is updated synchronously after a fill via increment_sync."""
    from datetime import date

    broker = MockBroker(order_id="KITE_CB")
    factory = _make_factory()
    sf = build_session_factory(engine)
    accountant = PositionAccountant(PositionStore(sf), factory)
    fill_handler = FillHandler(TradingStore(sf), accountant)
    reg = OrderExecutor(
        config=ExecConfig(),
        broker=broker,
        session_factory=sf,
        trading=TradingStore(sf),
        fill_handler=fill_handler,
    )

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)
    await reg.handle(make_validated(signal_id=sig_id))

    # Simulate a fill arriving (BUY fill reduces realized PnL by avg_price * qty)
    await reg.handle_fill(
        kite_order_id="KITE_CB",
        avg_price=150.0,
        filled_qty=10,
        symbol="INFY",
        instrument_type="EQUITY",
        side="BUY",
    )

    today = date.today()
    pnl_key = f"rf:pnl:{today.isoformat()}"
    cached = factory.pnl()._cache.get_sync(pnl_key)
    # BUY fill: sign = -1 → PnL = -150.0 * 10 = -1500.0
    assert cached is not None
    assert float(cached) == pytest.approx(-1500.0)


async def test_persist_order_status_retries_on_failure(engine: AsyncEngine) -> None:
    """Phase 3 DB write fails all 3 tenacity attempts; CRITICAL is logged, no exception raised."""
    import logging
    from unittest.mock import patch

    from sqlalchemy.exc import OperationalError

    broker = MockBroker(order_id="KITE_RETRY")
    sf = build_session_factory(engine)

    # Track how many times _attempt() is invoked inside _persist_order_status
    attempt_count = 0
    original_sf = sf

    class _FailingSession:
        """Context manager that raises OperationalError on enter."""
        async def __aenter__(self):
            nonlocal attempt_count
            attempt_count += 1
            raise OperationalError("simulated DB blip", None, None)

        async def __aexit__(self, *_):
            pass

    class _FailingSessionFactory:
        """Returns real session for Phase 1 (idempotency), failing sessions for Phase 3."""
        _real_calls = 0

        def __call__(self):
            self._real_calls += 1
            # Phase 1 gets real session; subsequent calls (Phase 3 retries) fail
            if self._real_calls <= 1:
                return original_sf()
            return _FailingSession()

    flaky_sf = _FailingSessionFactory()
    accountant = PositionAccountant(PositionStore(sf), _make_factory())
    fill_handler = FillHandler(TradingStore(sf), accountant)
    reg = OrderExecutor(
        config=ExecConfig(),
        broker=broker,
        session_factory=flaky_sf,  # type: ignore[arg-type]
        trading=TradingStore(sf),
        fill_handler=fill_handler,
    )

    sig_id = uuid4()
    await _insert_signal(engine, sig_id)

    with patch.object(logging.getLogger("trading.execution.order_executor"), "critical") as mock_crit:
        await reg.handle(make_validated(signal_id=sig_id))

    assert mock_crit.called
    assert "UNRECOVERABLE" in mock_crit.call_args[0][0]
    # tenacity retried 3 times → 3 failing session calls (calls 2, 3, 4)
    assert attempt_count == 3
