"""Tests for execution/service/stop_loss_monitor.py — check_stop_losses.

trading-platform#65: a price-based stop-loss exit monitor. This is Option 3
from the posted design writeup -- Option 2's periodic poll landed now as an
immediate backstop, routed through the real OrderExecutor.handle path (not
eod_square_off's paper-only apply_fill shortcut) so it actually reaches the
broker in live mode too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import build_session_factory, get_session, init_db
from trading.core.clock import SimulatedClock
from trading.core.schemas import Side, SignalType
from trading.execution.service.stop_loss_monitor import (
    STOP_LOSS_MONITOR_NAME,
    check_stop_losses,
)
from trading.execution.storage.models import Order, Position
from trading.execution.storage.store import TradingStore
from trading.strategy.storage.models import Signal

NOW = datetime(2026, 9, 16, 5, 0, tzinfo=UTC)


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def _clock() -> SimulatedClock:
    clock = SimulatedClock()
    clock.advance(NOW)
    return clock


async def _add_position(engine: AsyncEngine, symbol: str, net_qty: int, avg_price: str) -> None:
    async with get_session(engine) as s:
        s.add(
            Position(
                symbol=symbol,
                instrument_type="EQUITY",
                net_qty=net_qty,
                avg_price=Decimal(avg_price),
                updated_at=NOW,
            )
        )


async def _add_entry_signal(
    engine: AsyncEngine,
    symbol: str,
    *,
    side: str,
    stop_distance: str,
    strategy_id: str = "ema",
) -> None:
    async with get_session(engine) as s:
        s.add(
            Signal(
                id=uuid4(),
                strategy_id=strategy_id,
                algo_name=strategy_id,
                symbol=symbol,
                instrument_type="EQUITY",
                side=side,
                signal_type=SignalType.ENTRY.value,
                stop_distance=Decimal(stop_distance),
                created_at=NOW,
            )
        )


async def _add_pending_stop_loss_order(engine: AsyncEngine, symbol: str) -> None:
    async with get_session(engine) as s:
        signal = Signal(
            id=uuid4(),
            strategy_id=STOP_LOSS_MONITOR_NAME,
            algo_name=STOP_LOSS_MONITOR_NAME,
            symbol=symbol,
            instrument_type="EQUITY",
            side="SELL",
            signal_type=SignalType.EXIT.value,
            stop_distance=Decimal(0),
            created_at=NOW,
        )
        s.add(signal)
        await s.flush()
        s.add(
            Order(
                id=uuid4(),
                kite_order_id=f"PENDING_{uuid4().hex[:8]}",
                signal_id=signal.id,
                status="PENDING",
                qty=1,
                avg_price=Decimal(0),
                created_at=NOW,
            )
        )


def _price_store(prices: dict[str, float]) -> MagicMock:
    def _get(symbol: str) -> float | None:
        return prices.get(symbol)

    store = MagicMock()
    store.get.side_effect = _get
    return store


async def test_no_open_positions_does_nothing(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    await check_stop_losses(trading, order_executor, _price_store({}), _clock())

    order_executor.handle.assert_not_called()


async def test_long_position_within_stop_does_nothing(engine: AsyncEngine) -> None:
    await _add_position(engine, "INFY", net_qty=10, avg_price="100.00")
    await _add_entry_signal(engine, "INFY", side="BUY", stop_distance="5")
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    # last_price 96 is above the stop level of 95 (100 - 5) -- not breached.
    await check_stop_losses(trading, order_executor, _price_store({"INFY": 96.0}), _clock())

    order_executor.handle.assert_not_called()


async def test_long_position_breach_routes_exit_through_order_executor(
    engine: AsyncEngine,
) -> None:
    await _add_position(engine, "INFY", net_qty=10, avg_price="100.00")
    await _add_entry_signal(engine, "INFY", side="BUY", stop_distance="5")
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    # last_price 94 is below the stop level of 95 (100 - 5) -- breached.
    await check_stop_losses(trading, order_executor, _price_store({"INFY": 94.0}), _clock())

    order_executor.handle.assert_called_once()
    event = order_executor.handle.call_args[0][0]
    assert event.side == Side.SELL
    assert event.quantity == 10
    assert event.symbol == "INFY"
    assert event.signal_type == SignalType.EXIT
    assert event.strategy_id == STOP_LOSS_MONITOR_NAME
    assert event.algo_name == STOP_LOSS_MONITOR_NAME

    # The synthetic exit signal was actually persisted (audit trail), not
    # just passed to the mock -- OrderExecutor.handle requires the Signal
    # row to already exist (it never calls save_signal itself).
    async with get_session(engine) as s:
        from sqlalchemy import select

        result = await s.execute(select(Signal).where(Signal.id == event.signal_id))
        saved = result.scalar_one()
        assert saved.side == "SELL"


async def test_short_position_breach_exits_with_buy(engine: AsyncEngine) -> None:
    await _add_position(engine, "TCS", net_qty=-5, avg_price="3500.00")
    await _add_entry_signal(engine, "TCS", side="SELL", stop_distance="20")
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    # last_price 3525 is above the stop level of 3520 (3500 + 20) -- breached.
    await check_stop_losses(trading, order_executor, _price_store({"TCS": 3525.0}), _clock())

    order_executor.handle.assert_called_once()
    event = order_executor.handle.call_args[0][0]
    assert event.side == Side.BUY
    assert event.quantity == 5


async def test_zero_stop_distance_entry_is_treated_as_unmonitored(engine: AsyncEngine) -> None:
    """A stop_distance of 0 (e.g. eod_square_off's own synthetic entries, or
    an entry that genuinely had no stop configured) must not be treated as
    'breach on any move' -- it means no stop is set for this position."""
    await _add_position(engine, "INFY", net_qty=10, avg_price="100.00")
    await _add_entry_signal(engine, "INFY", side="BUY", stop_distance="0")
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    await check_stop_losses(trading, order_executor, _price_store({"INFY": 1.0}), _clock())

    order_executor.handle.assert_not_called()


async def test_no_entry_signal_is_skipped_not_errored(engine: AsyncEngine) -> None:
    """A position with no ENTRY signal on record (shouldn't normally happen,
    but must not crash the monitor if it does)."""
    await _add_position(engine, "INFY", net_qty=10, avg_price="100.00")
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    await check_stop_losses(trading, order_executor, _price_store({"INFY": 1.0}), _clock())

    order_executor.handle.assert_not_called()


async def test_no_price_available_is_skipped(engine: AsyncEngine) -> None:
    await _add_position(engine, "INFY", net_qty=10, avg_price="100.00")
    await _add_entry_signal(engine, "INFY", side="BUY", stop_distance="5")
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    await check_stop_losses(trading, order_executor, _price_store({}), _clock())

    order_executor.handle.assert_not_called()


async def test_pending_exit_already_in_flight_is_not_duplicated(engine: AsyncEngine) -> None:
    """If a prior round's exit for this position is still PENDING with the
    broker, a fresh poll must not fire a second exit order on top of it."""
    await _add_position(engine, "INFY", net_qty=10, avg_price="100.00")
    await _add_entry_signal(engine, "INFY", side="BUY", stop_distance="5")
    await _add_pending_stop_loss_order(engine, "INFY")
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    await check_stop_losses(trading, order_executor, _price_store({"INFY": 90.0}), _clock())

    order_executor.handle.assert_not_called()


async def test_most_recent_entry_signal_stop_distance_used(engine: AsyncEngine) -> None:
    """Two ENTRY signals on the same symbol (averaged-in position, or a
    closed-then-reopened one) -- the most recent one's stop_distance wins,
    per the design writeup's documented first-cut simplification."""
    async with get_session(engine) as s:
        s.add(
            Signal(
                id=uuid4(),
                strategy_id="ema",
                algo_name="ema",
                symbol="INFY",
                instrument_type="EQUITY",
                side="BUY",
                signal_type=SignalType.ENTRY.value,
                stop_distance=Decimal("50"),
                created_at=datetime(2026, 9, 16, 3, 0, tzinfo=UTC),
            )
        )
    async with get_session(engine) as s:
        s.add(
            Signal(
                id=uuid4(),
                strategy_id="ema",
                algo_name="ema",
                symbol="INFY",
                instrument_type="EQUITY",
                side="BUY",
                signal_type=SignalType.ENTRY.value,
                stop_distance=Decimal("5"),
                created_at=datetime(2026, 9, 16, 4, 0, tzinfo=UTC),
            )
        )
    await _add_position(engine, "INFY", net_qty=10, avg_price="100.00")
    sf = build_session_factory(engine)
    trading = TradingStore(sf)
    order_executor = MagicMock()
    order_executor.handle = AsyncMock()

    # 94 breaches the more recent stop_distance=5 (stop at 95) but not the
    # older stop_distance=50 (stop at 50) -- confirms the "most recent" pick.
    await check_stop_losses(trading, order_executor, _price_store({"INFY": 94.0}), _clock())

    order_executor.handle.assert_called_once()
