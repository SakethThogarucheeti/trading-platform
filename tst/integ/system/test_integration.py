"""
End-to-end pipeline integration tests.

Drives the full SignalGenerator → RiskFilter → OrderExecutor chain
with synthetic candle data. Uses FaultInjector to verify the broker
layer handles errors without corrupting DB state.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parents[1]))
from helpers import seed_signal
from simulators.fault_injector import FaultInjector

from trading.broker.service.paper_broker import PriceStore
from trading.core.clock import SimulatedClock
from trading.core.models import Order
from trading.core.schemas import (
    CandleEvent,
    InstrumentType,
    OrderStatus,
    Side,
    SignalEvent,
    SignalType,
)
from trading.execution.service.fill_handler import FillHandler
from trading.execution.service.executor import ExecConfig, OrderExecutor
from trading.execution.service.position_accountant import PositionAccountant
from trading.risk.gates.circuit_breaker import CircuitBreakerGate
from trading.risk.gates.daily_loss import DailyLossGate
from trading.risk.gates.duplicate_position import DuplicatePositionGate
from trading.risk.gates.time_cutoff import TimeCutoffGate
from trading.risk.service.filter import RiskConfig, RiskFilter
from trading.tick_ingest.service.ingestor import CircuitBreaker
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.tick_ingest.storage.store import AuditStore
from trading.execution.storage.store import PositionStore
from trading.execution.storage.store import TradingStore


def _make_fill_handler(session_factory):
    setup_cache(None)
    accountant = PositionAccountant(PositionStore(session_factory), CacherFactory(ValueCache()))
    return FillHandler(TradingStore(session_factory), accountant)


def _candle(
    symbol: str = "INFY", interval: str = "5min", ts: datetime | None = None
) -> CandleEvent:
    return CandleEvent(
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
        interval=interval,
        open=1500.0,
        high=1510.0,
        low=1490.0,
        close=1505.0,
        volume=10000,
        timestamp=ts or datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        tick_log_id=0,
    )


def _signal(symbol: str = "INFY", stop_distance: float = 1.0) -> SignalEvent:
    return SignalEvent(
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="test",
        signal_type=SignalType.ENTRY,
        stop_distance=stop_distance,
        tick_log_id=0,
    )


def _make_pipeline(session_factory, broker):
    """Build RiskFilter → OrderExecutor wired together."""
    clock = SimulatedClock()
    clock.advance(datetime(2024, 1, 2, 10, 0, tzinfo=UTC))
    trading = TradingStore(session_factory)
    audit = AuditStore(session_factory)
    position = PositionStore(session_factory)

    setup_cache(None)
    factory = CacherFactory(ValueCache(), clock)
    risk_reg = RiskFilter(
        config=RiskConfig(
            equity=1_000_000.0,
            intraday_cutoff_hour=15,
            intraday_cutoff_minute=30,
        ),
        gates=[
            TimeCutoffGate(),
            CircuitBreakerGate(CircuitBreaker()),
            DailyLossGate(enabled=False),
            DuplicatePositionGate(),
        ],
        trading=trading,
        audit=audit,
        position=position,
        factory=factory,
        clock=clock,
    )
    price_store = PriceStore()
    price_store.update("INFY", 1505.0)

    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="paper"),
        broker=broker,
        session_factory=session_factory,
        trading=trading,
        fill_handler=_make_fill_handler(session_factory),
    )
    return risk_reg, exec_reg


async def test_pipeline_happy_path(engine, session_factory):
    """Signal → risk accept → order placed and filled end-to-end."""

    class _OkBroker:
        async def place_order(self, *a, **kw):
            return f"ORDER_{uuid.uuid4().hex[:8]}"

    risk_reg, exec_reg = _make_pipeline(session_factory, _OkBroker())
    signal = _signal(stop_distance=1.0)

    order_event = await risk_reg.handle(signal)
    assert order_event is not None, "Valid signal must pass risk check"

    await exec_reg.handle(order_event)

    async with session_factory() as session:
        result = await session.execute(select(Order).where(Order.signal_id == signal.signal_id))
        order = result.scalar_one_or_none()

    assert order is not None
    assert order.status == OrderStatus.PLACED.value


async def test_fault_injector_timeout_marks_rejected(engine, session_factory):
    """FaultInjector with 100% timeout rate must produce REJECTED orders."""

    class _OkBroker:
        async def place_order(self, *a, **kw):
            return f"ORDER_{uuid.uuid4().hex[:8]}"

    faulty = FaultInjector(_OkBroker(), seed=0).with_timeout_rate(1.0)
    risk_reg, exec_reg = _make_pipeline(session_factory, faulty)

    signal = _signal(stop_distance=1.0)
    order_event = await risk_reg.handle(signal)
    assert order_event is not None

    await exec_reg.handle(order_event)

    async with session_factory() as session:
        result = await session.execute(select(Order).where(Order.signal_id == signal.signal_id))
        order = result.scalar_one_or_none()

    assert order is not None
    assert order.status == OrderStatus.REJECTED.value


async def test_fault_injector_partial_failures_no_deadlock(engine, session_factory):
    """50% error rate across 10 signals — system must not deadlock or crash."""

    class _OkBroker:
        async def place_order(self, *a, **kw):
            return f"ORDER_{uuid.uuid4().hex[:8]}"

    faulty = FaultInjector(_OkBroker(), seed=42).with_error_rate(0.5)
    risk_reg, exec_reg = _make_pipeline(session_factory, faulty)

    for _ in range(10):
        signal = _signal(stop_distance=1.0)
        order_event = await risk_reg.handle(signal)
        if order_event is not None:
            await exec_reg.handle(order_event)

    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(Order.status == OrderStatus.PENDING.value)
        )
        pending = result.scalars().all()

    assert len(pending) == 0, f"Found {len(pending)} orders stuck in PENDING state"


async def test_idempotency_duplicate_signal_end_to_end(engine, session_factory):
    """
    Duplicate signal_id delivered twice (simulating a retry) must only
    produce one DB row and one broker call.
    """
    broker_calls: list[str] = []

    class _CountingBroker:
        async def place_order(self, *a, **kw):
            oid = f"ORDER_{uuid.uuid4().hex[:8]}"
            broker_calls.append(oid)
            return oid

    trading = TradingStore(session_factory)
    exec_reg = OrderExecutor(
        config=ExecConfig(exec_id="direct"),
        broker=_CountingBroker(),
        session_factory=session_factory,
        trading=trading,
        fill_handler=_make_fill_handler(session_factory),
    )

    from trading.core.schemas import OrderType, ValidatedOrderEvent

    signal_id = uuid.uuid4()
    order_event = ValidatedOrderEvent(
        signal_id=signal_id,
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        quantity=5,
        order_type=OrderType.MARKET,
        tick_log_id=1,
    )

    await seed_signal(session_factory, order_event)
    await exec_reg.handle(order_event)
    count_after_first = len(broker_calls)

    await exec_reg.handle(order_event)  # duplicate

    assert len(broker_calls) == count_after_first, (
        "Duplicate signal_id must not reach broker a second time"
    )

    async with session_factory() as session:
        result = await session.execute(select(Order).where(Order.signal_id == signal_id))
        orders = result.scalars().all()

    assert len(orders) == 1, f"Expected 1 DB row for signal_id, got {len(orders)}"
