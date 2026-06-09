"""Tests for execution/position_accountant.py — PositionAccountant"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, call
from zoneinfo import ZoneInfo

import pytest

from trading.core.clock import Clock, SYSTEM_CLOCK
from trading.core.schemas import FillEvent, Side
from trading.execution.service.position_accountant import PositionAccountant
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.execution.api.interfaces import AbstractPositionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fill(avg_price: float = 100.0, qty: int = 10) -> FillEvent:
    return FillEvent(
        kite_order_id="KITE_001",
        avg_price=avg_price,
        filled_qty=qty,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )


class _FixedClock(Clock):
    def __init__(self, dt: datetime) -> None:
        self._dt = dt

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo("UTC")

    def now(self) -> datetime:
        return self._dt


def _make_accountant(
    position: AbstractPositionStore | None = None,
    factory: CacherFactory | None = None,
    clock: Clock | None = None,
) -> PositionAccountant:
    mock_position = position or MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()

    if factory is None:
        setup_cache(None)
        factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)

    return PositionAccountant(
        position=mock_position,
        factory=factory,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_apply_fill_calls_update_position() -> None:
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()

    setup_cache(None)
    factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)
    accountant = PositionAccountant(position=mock_position, factory=factory)

    fill = _make_fill()
    await accountant.apply_fill(fill, Side.BUY, "INFY", "EQUITY")

    mock_position.update_position.assert_called_once_with(fill, Side.BUY, "INFY", "EQUITY")


async def test_apply_fill_increments_pnl_cache() -> None:
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()

    setup_cache(None)
    factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)
    fixed_date = date(2025, 1, 6)
    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(position=mock_position, factory=factory, clock=clock)

    fill = _make_fill(avg_price=150.0, qty=10)
    await accountant.apply_fill(fill, Side.BUY, "INFY", "EQUITY")

    pnl_key = f"rf:pnl:{fixed_date.isoformat()}"
    cached = factory.pnl()._cache.get_sync(pnl_key)
    assert cached is not None
    # BUY: sign = -1 → -150.0 * 10
    assert float(cached) == pytest.approx(-1500.0)


async def test_apply_fill_invalidates_api_cache() -> None:
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()

    mock_api = MagicMock()
    mock_api.invalidate_pnl = AsyncMock()
    mock_factory = MagicMock(spec=CacherFactory)
    mock_factory.pnl.return_value = MagicMock(increment_sync=MagicMock())
    mock_factory.api.return_value = mock_api

    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(position=mock_position, factory=mock_factory, clock=clock)

    fill = _make_fill()
    await accountant.apply_fill(fill, Side.BUY, "INFY", "EQUITY")

    mock_api.invalidate_pnl.assert_called_once_with(date(2025, 1, 6))


async def test_apply_fill_sequencing() -> None:
    """DB update (update_position) must fire before cache operations."""
    call_order: list[str] = []

    mock_position = MagicMock(spec=AbstractPositionStore)

    async def _record_position(*a, **kw) -> None:
        call_order.append("db")

    mock_position.update_position = _record_position

    mock_pnl = MagicMock()

    def _record_pnl(*a, **kw) -> None:
        call_order.append("pnl")

    mock_pnl.increment_sync = _record_pnl

    mock_api = MagicMock()

    async def _record_api(*a, **kw) -> None:
        call_order.append("api")

    mock_api.invalidate_pnl = _record_api

    mock_factory = MagicMock(spec=CacherFactory)
    mock_factory.pnl.return_value = mock_pnl
    mock_factory.api.return_value = mock_api

    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(position=mock_position, factory=mock_factory, clock=clock)

    await accountant.apply_fill(_make_fill(), Side.BUY, "INFY", "EQUITY")

    assert call_order == ["db", "pnl", "api"]
