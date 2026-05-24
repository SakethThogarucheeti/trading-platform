"""
Risk filter guardrail tests.

Verifies that RiskFilter correctly enforces:
- Intraday time cutoff
- Duplicate position guard
- Zero quantity rejection
- Valid signal passes through
"""

from __future__ import annotations

from trading.core.clock import SimulatedClock
from trading.core.schemas import (
    InstrumentType,
    Side,
    SignalEvent,
    SignalType,
    ValidatedOrderEvent,
)
from trading.risk.gates.circuit_breaker import CircuitBreakerGate
from trading.risk.gates.daily_loss import DailyLossGate
from trading.risk.gates.duplicate_position import DuplicatePositionGate
from trading.risk.gates.time_cutoff import TimeCutoffGate
from trading.risk.risk_filter import RiskConfig, RiskFilter
from trading.tick_ingest.tick_ingestor import CircuitBreaker
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.position import PositionStore
from trading.storage.stores.trading import TradingStore


def _signal(
    symbol: str = "INFY",
    side: Side = Side.BUY,
    stop_distance: float = 10.0,
) -> SignalEvent:
    return SignalEvent(
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
        side=side,
        strategy_id="test",
        signal_type=SignalType.ENTRY,
        stop_distance=stop_distance,
        tick_log_id=0,
    )


def _make_risk_reg(
    session_factory,
    *,
    cutoff_hour: int = 23,
    cutoff_minute: int = 59,
    equity: float = 1_000_000.0,
    clock=None,
) -> RiskFilter:
    trading = TradingStore(session_factory)
    audit = AuditStore(session_factory)
    position = PositionStore(session_factory)
    return RiskFilter(
        config=RiskConfig(
            equity=equity,
            max_daily_loss_pct=2.0,
            risk_per_trade_pct=1.0,
            rc_id="default",
            intraday_cutoff_hour=cutoff_hour,
            intraday_cutoff_minute=cutoff_minute,
        ),
        gates=[
            TimeCutoffGate(),
            CircuitBreakerGate(CircuitBreaker()),
            DailyLossGate(enabled=False),  # paper mode: skip daily loss check
            DuplicatePositionGate(),
        ],
        trading=trading,
        audit=audit,
        position=position,
        clock=clock,
    )


async def test_time_cutoff_rejects_signal(engine, session_factory):
    """Signals when clock is past intraday cutoff must be rejected (return None)."""
    from datetime import UTC, datetime

    clock = SimulatedClock()
    clock.advance(datetime(2024, 1, 2, 16, 0, 0, tzinfo=UTC))

    risk_reg = _make_risk_reg(session_factory, cutoff_hour=15, cutoff_minute=30, clock=clock)
    result = await risk_reg.handle(_signal())

    assert result is None, "Signal after cutoff must be rejected"


async def test_valid_signal_passes_through(engine, session_factory):
    """A valid signal within trading hours must produce a ValidatedOrderEvent."""
    from datetime import UTC, datetime

    clock = SimulatedClock()
    clock.advance(datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC))

    risk_reg = _make_risk_reg(
        session_factory, cutoff_hour=15, cutoff_minute=30, equity=1_000_000.0, clock=clock
    )
    result = await risk_reg.handle(_signal(stop_distance=1.0))

    assert isinstance(result, ValidatedOrderEvent), "Valid signal must produce ValidatedOrderEvent"
    assert result.quantity > 0


async def test_zero_quantity_rejected(engine, session_factory):
    """Signal with enormous stop_distance → qty=0 → must be rejected."""
    from datetime import UTC, datetime

    clock = SimulatedClock()
    clock.advance(datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC))

    risk_reg = _make_risk_reg(
        session_factory,
        cutoff_hour=15,
        cutoff_minute=30,
        equity=1_000.0,
        clock=clock,
    )
    # stop_distance=100_000 → risk_amount=10 → qty = 10/100_000 = 0
    result = await risk_reg.handle(_signal(stop_distance=100_000.0))

    assert result is None, "Zero-quantity signal must be rejected"


async def test_circuit_open_rejects_signal(engine, session_factory):
    """When circuit breaker is open, all signals must be rejected."""
    from datetime import UTC, datetime

    clock = SimulatedClock()
    clock.advance(datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC))

    circuit = CircuitBreaker()
    circuit.open()

    trading = TradingStore(session_factory)
    audit = AuditStore(session_factory)
    position = PositionStore(session_factory)
    risk_reg = RiskFilter(
        config=RiskConfig(
            equity=1_000_000.0,
            intraday_cutoff_hour=15,
            intraday_cutoff_minute=30,
        ),
        gates=[
            TimeCutoffGate(),
            CircuitBreakerGate(circuit),
            DailyLossGate(enabled=False),
            DuplicatePositionGate(),
        ],
        trading=trading,
        audit=audit,
        position=position,
        clock=clock,
    )
    result = await risk_reg.handle(_signal())

    assert result is None, "Open circuit breaker must reject all signals"


async def test_duplicate_position_rejected(engine, session_factory):
    """A second BUY signal when already long must be rejected."""
    from datetime import UTC, datetime

    clock = SimulatedClock()
    clock.advance(datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC))

    risk_reg = _make_risk_reg(
        session_factory, cutoff_hour=15, cutoff_minute=30, equity=1_000_000.0, clock=clock
    )

    # First BUY — accepted, creates a position
    first = await risk_reg.handle(_signal(stop_distance=1.0))
    assert first is not None

    # Manually write the position to DB so the duplicate check fires
    from datetime import UTC
    from datetime import datetime as dt
    from decimal import Decimal

    from trading.core.models import Position

    async with session_factory() as session:
        async with session.begin():
            session.add(
                Position(
                    symbol="INFY",
                    instrument_type=InstrumentType.EQUITY.value,
                    net_qty=first.quantity,
                    avg_price=Decimal("1500"),
                    updated_at=dt.now(UTC),
                )
            )

    # Second BUY — must be rejected (already in position)
    second = await risk_reg.handle(_signal(stop_distance=1.0))
    assert second is None, "Duplicate position must be rejected"
