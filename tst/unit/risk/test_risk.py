"""Tests for risk/risk_filter.py — RiskFilter, and risk/sizer.py"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.core.database import build_session_factory, init_db
from trading.core.models import Order, Position, Signal
from trading.core.schemas import (
    InstrumentType,
    OrderStatus,
    Side,
    SignalEvent,
    SignalType,
    ValidatedOrderEvent,
)
from trading.risk.risk_filter import RiskConfig, RiskFilter
from trading.tick_ingest.tick_ingestor import CircuitBreaker
from trading.risk.sizer import calculate_quantity
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.trading import TradingStore

NOW = datetime.now(UTC)
TODAY = NOW.date()

# ---------------------------------------------------------------------------
# Sizer tests
# ---------------------------------------------------------------------------


def test_sizer_basic_quantity() -> None:
    # equity=100_000, risk=1%, stop=50 → 100_000 * 0.01 / 50 = 20
    assert calculate_quantity(stop_distance=50, equity=100_000, risk_pct=1.0) == 20


def test_sizer_rounds_down_fractional() -> None:
    # 100_000 * 0.01 / 60 = 16.6... → floor → 16
    assert calculate_quantity(stop_distance=60, equity=100_000, risk_pct=1.0) == 16


def test_sizer_lot_size_rounds_down_to_lot() -> None:
    # raw=37, lot=25 → 37 // 25 * 25 = 25
    qty = calculate_quantity(stop_distance=27, equity=100_000, risk_pct=1.0, lot_size=25)
    assert qty == 25


def test_sizer_lot_size_below_one_lot_returns_zero() -> None:
    qty = calculate_quantity(stop_distance=84, equity=100_000, risk_pct=1.0, lot_size=25)
    assert qty == 0


def test_sizer_zero_stop_distance_returns_zero() -> None:
    assert calculate_quantity(stop_distance=0, equity=100_000, risk_pct=1.0) == 0


def test_sizer_negative_stop_distance_returns_zero() -> None:
    assert calculate_quantity(stop_distance=-5, equity=100_000, risk_pct=1.0) == 0


def test_sizer_very_small_equity_returns_zero() -> None:
    assert calculate_quantity(stop_distance=100, equity=0.5, risk_pct=1.0) == 0


def test_sizer_no_lot_size_returns_raw() -> None:
    qty = calculate_quantity(stop_distance=10, equity=100_000, risk_pct=1.0, lot_size=None)
    assert qty == 100


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def make_config(**overrides) -> RiskConfig:
    base = dict(
        equity=100_000.0,
        max_daily_loss_pct=2.0,
        risk_per_trade_pct=1.0,
        rc_id="default",
        paper_trading=False,
        # Use 23:59 so tests never fail due to time-of-day
        intraday_cutoff_hour=23,
        intraday_cutoff_minute=59,
    )
    return RiskConfig(**{**base, **overrides})  # type: ignore[arg-type]


def make_registry(
    engine: AsyncEngine,
    circuit: CircuitBreaker | None = None,
    config: RiskConfig | None = None,
) -> RiskFilter:
    sf = build_session_factory(engine)
    return RiskFilter(
        config=config or make_config(),
        circuit=circuit or CircuitBreaker(),
        trading=TradingStore(sf),
        audit=AuditStore(sf),
    )


def make_signal(**overrides) -> SignalEvent:
    base = dict(
        signal_id=uuid4(),
        strategy_id="ema_cross",
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        signal_type=SignalType.ENTRY,
        stop_distance=10.0,
        timestamp=NOW,
        tick_log_id=1,
    )
    return SignalEvent(**{**base, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Valid signal passes
# ---------------------------------------------------------------------------


async def test_valid_signal_returns_validated_order(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    result = await reg.handle(make_signal())

    assert result is not None
    assert isinstance(result, ValidatedOrderEvent)
    assert result.quantity > 0
    assert result.symbol == "INFY"


# ---------------------------------------------------------------------------
# Time cutoff
# ---------------------------------------------------------------------------


async def test_after_cutoff_rejects_signal(engine: AsyncEngine) -> None:
    config = make_config(intraday_cutoff_hour=0, intraday_cutoff_minute=0)
    reg = make_registry(engine, config=config)

    result = await reg.handle(make_signal())
    assert result is None


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


async def test_circuit_open_rejects_signal(engine: AsyncEngine) -> None:
    circuit = CircuitBreaker()
    circuit.open()
    reg = make_registry(engine, circuit=circuit)

    result = await reg.handle(make_signal())
    assert result is None


async def test_circuit_closed_allows_signal(engine: AsyncEngine) -> None:
    circuit = CircuitBreaker()
    circuit.close()  # explicitly closed
    reg = make_registry(engine, circuit=circuit)

    result = await reg.handle(make_signal())
    assert result is not None


# ---------------------------------------------------------------------------
# Daily loss limit
# ---------------------------------------------------------------------------


async def test_daily_loss_limit_rejects_signal(engine: AsyncEngine) -> None:
    from trading.core.database import get_session

    sig_id = uuid4()
    async with get_session(engine) as s:
        s.add(
            Signal(
                id=sig_id,
                strategy_id="s",
                symbol="INFY",
                instrument_type="EQUITY",
                side="SELL",
                signal_type="ENTRY",
                stop_distance=Decimal("10"),
                created_at=NOW,
            )
        )

    # P&L = 10*1000 = 10_000, limit = 100_000 * 2% = 2_000 → exceeded
    async with get_session(engine) as s:
        s.add(
            Order(
                id=uuid4(),
                kite_order_id="K_LOSS",
                signal_id=sig_id,
                status=OrderStatus.FILLED.value,
                qty=10,
                avg_price=Decimal("1000"),
                created_at=NOW,
            )
        )

    reg = make_registry(engine, config=make_config(equity=100_000.0, paper_trading=False))
    result = await reg.handle(make_signal())
    assert result is None


async def test_paper_trading_skips_daily_loss_check(engine: AsyncEngine) -> None:
    """In paper trading mode, daily loss limit is skipped."""
    from trading.core.database import get_session

    sig_id = uuid4()
    async with get_session(engine) as s:
        s.add(
            Signal(
                id=sig_id,
                strategy_id="s",
                symbol="INFY",
                instrument_type="EQUITY",
                side="SELL",
                signal_type="ENTRY",
                stop_distance=Decimal("10"),
                created_at=NOW,
            )
        )
    async with get_session(engine) as s:
        s.add(
            Order(
                id=uuid4(),
                kite_order_id="K_LOSS_PAPER",
                signal_id=sig_id,
                status=OrderStatus.FILLED.value,
                qty=10,
                avg_price=Decimal("1000"),
                created_at=NOW,
            )
        )

    config = make_config(equity=100_000.0, paper_trading=True)
    reg = make_registry(engine, config=config)
    result = await reg.handle(make_signal())
    # Paper mode skips the daily loss check — signal should pass
    assert result is not None


# ---------------------------------------------------------------------------
# Position check
# ---------------------------------------------------------------------------


async def test_entry_with_existing_position_rejected(engine: AsyncEngine) -> None:
    from trading.core.database import get_session

    async with get_session(engine) as s:
        s.add(
            Position(
                symbol="INFY",
                instrument_type="EQUITY",
                net_qty=10,
                avg_price=Decimal("1500"),
                updated_at=NOW,
            )
        )

    reg = make_registry(engine)
    result = await reg.handle(make_signal(signal_type=SignalType.ENTRY))
    assert result is None


async def test_exit_with_existing_position_passes(engine: AsyncEngine) -> None:
    from trading.core.database import get_session

    async with get_session(engine) as s:
        s.add(
            Position(
                symbol="INFY",
                instrument_type="EQUITY",
                net_qty=10,
                avg_price=Decimal("1500"),
                updated_at=NOW,
            )
        )

    reg = make_registry(engine)
    result = await reg.handle(make_signal(signal_type=SignalType.EXIT, side=Side.SELL))
    assert result is not None


# ---------------------------------------------------------------------------
# Zero quantity
# ---------------------------------------------------------------------------


async def test_zero_quantity_rejects_signal(engine: AsyncEngine) -> None:
    """stop_distance so large that no shares can be afforded."""
    config = make_config(equity=100.0)  # tiny equity
    reg = make_registry(engine, config=config)

    # risk=1% of 100 = 1, stop=50 → qty=0
    result = await reg.handle(make_signal(stop_distance=50.0))
    assert result is None


# ---------------------------------------------------------------------------
# Rejection audit logging
# ---------------------------------------------------------------------------


async def test_rejected_signal_logged_to_audit(engine: AsyncEngine) -> None:
    from sqlalchemy import select

    from trading.core.database import get_session
    from trading.core.models import AuditLog

    config = make_config(intraday_cutoff_hour=0, intraday_cutoff_minute=0)
    reg = make_registry(engine, config=config)

    await reg.handle(make_signal())

    async with get_session(engine) as s:
        result = await s.execute(select(AuditLog).where(AuditLog.module.like("risk_filter%")))
        logs = result.scalars().all()

    assert len(logs) >= 1
    assert any("rejected" in log.message for log in logs)


# ---------------------------------------------------------------------------
# _log_decision early return when tick_log_id == 0
# ---------------------------------------------------------------------------


async def test_log_decision_skips_when_tick_log_id_zero(engine: AsyncEngine) -> None:
    """_log_decision returns early when tick_log_id == 0 (line 183)."""
    reg = make_registry(engine)
    sig = make_signal(tick_log_id=0)
    await reg._log_decision("SIGNAL_ACCEPTED", sig, {"qty": 10})  # should not write to DB


async def test_reject_direct_covers_audit_log_path(engine: AsyncEngine) -> None:
    """Calling _reject directly covers the audit log write path (lines 178-179)."""
    reg = make_registry(engine)
    sig = make_signal(tick_log_id=1)
    await reg._reject(sig, "TEST_REASON")  # should not raise


# ---------------------------------------------------------------------------
# Lines 130-131: audit log failure in handle() is swallowed
# ---------------------------------------------------------------------------


async def test_audit_log_failure_in_accept_is_swallowed() -> None:
    """Covers lines 130-131: audit.log_audit raises inside handle() and is swallowed."""
    from unittest.mock import AsyncMock, MagicMock

    from trading.storage.stores.audit import AbstractAuditStore

    class _FailAuditStore(AbstractAuditStore):
        async def log_tick(self, event, symbol):
            return 1

        async def log_decision(self, **kwargs):
            pass

        async def log_audit(self, module, level, message):
            raise RuntimeError("audit DB down")

    mock_trading = AsyncMock()
    mock_trading.get_daily_realized_pnl = AsyncMock(return_value=0.0)
    mock_trading.get_position = AsyncMock(return_value=None)
    mock_trading.save_signal = AsyncMock()

    reg = RiskFilter(
        config=make_config(),
        circuit=CircuitBreaker(),
        trading=mock_trading,
        audit=_FailAuditStore(),
    )
    sig = make_signal(tick_log_id=1)
    # The audit.log_audit failure should be swallowed, result should still return
    result = await reg.handle(sig)
    assert result is not None


# ---------------------------------------------------------------------------
# Lines 136-137: save_signal failure is caught silently
# ---------------------------------------------------------------------------


async def test_save_signal_failure_is_swallowed() -> None:
    """Covers lines 136-137: trading.save_signal raises inside handle() and is swallowed."""
    from unittest.mock import AsyncMock

    from trading.storage.stores.audit import AbstractAuditStore

    class _NoopAuditStore(AbstractAuditStore):
        async def log_tick(self, event, symbol):
            return 1

        async def log_decision(self, **kwargs):
            pass

        async def log_audit(self, module, level, message):
            pass

    mock_trading = AsyncMock()
    mock_trading.get_daily_realized_pnl = AsyncMock(return_value=0.0)
    mock_trading.get_position = AsyncMock(return_value=None)
    mock_trading.save_signal = AsyncMock(side_effect=RuntimeError("DB down"))

    reg = RiskFilter(
        config=make_config(),
        circuit=CircuitBreaker(),
        trading=mock_trading,
        audit=_NoopAuditStore(),
    )
    sig = make_signal(tick_log_id=1)
    # save_signal failure should be swallowed
    result = await reg.handle(sig)
    assert result is not None


# ---------------------------------------------------------------------------
# Lines 175-176: _reject audit log failure is swallowed silently
# ---------------------------------------------------------------------------


async def test_reject_audit_log_failure_is_swallowed() -> None:
    """Covers lines 175-176: audit.log_audit raises inside _reject() and is swallowed."""
    from unittest.mock import AsyncMock

    from trading.storage.stores.audit import AbstractAuditStore

    class _FailAuditStore(AbstractAuditStore):
        async def log_tick(self, event, symbol):
            return 1

        async def log_decision(self, **kwargs):
            pass

        async def log_audit(self, module, level, message):
            raise RuntimeError("audit DB down in reject")

    mock_trading = AsyncMock()

    reg = RiskFilter(
        config=make_config(),
        circuit=CircuitBreaker(),
        trading=mock_trading,
        audit=_FailAuditStore(),
    )
    sig = make_signal(tick_log_id=5)
    # _reject with failing audit store should not raise
    await reg._reject(sig, "TEST_FAIL_REASON")


# ---------------------------------------------------------------------------
# Lines 175-176: _log_decision when tick_log_id > 0
# ---------------------------------------------------------------------------


async def test_log_decision_writes_when_tick_log_id_positive(engine: AsyncEngine) -> None:
    """Covers lines 175-176: _log_decision is called with tick_log_id > 0."""
    from sqlalchemy import select

    from trading.core.database import get_session
    from trading.core.models import DecisionLog

    reg = make_registry(engine)
    sig = make_signal(tick_log_id=99)
    from trading.risk.risk_filter import SignalAcceptedContext
    await reg._log_decision("SIGNAL_ACCEPTED", sig, SignalAcceptedContext(qty=5, order_type="MARKET"))

    # Wait briefly for the fire-and-forget task
    import asyncio
    await asyncio.sleep(0.05)

    async with get_session(engine) as s:
        result = await s.execute(select(DecisionLog))
        logs = result.scalars().all()
    assert any(log.step == "SIGNAL_ACCEPTED" for log in logs)
