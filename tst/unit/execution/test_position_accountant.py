"""Tests for execution/position_accountant.py — PositionAccountant"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from trading.core.clock import SYSTEM_CLOCK, Clock
from trading.core.schemas import FillEvent, Side
from trading.execution.api.interfaces import AbstractPositionStore, AbstractTradingStore
from trading.execution.service.position_accountant import PositionAccountant
from trading.storage.cache import CacherFactory, ValueCache

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


def _make_factory() -> CacherFactory:
    return CacherFactory(ValueCache(), SYSTEM_CLOCK)


def _make_trading() -> AbstractTradingStore:
    mock = MagicMock(spec=AbstractTradingStore)
    mock.increment_pnl_aggregate = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_apply_fill_calls_update_position() -> None:
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()

    accountant = PositionAccountant(
        position=mock_position, trading=_make_trading(), factory=_make_factory()
    )

    fill = _make_fill()
    await accountant.apply_fill(fill, Side.BUY, "INFY", "EQUITY")

    mock_position.update_position.assert_called_once_with(fill, Side.BUY, "INFY", "EQUITY")


async def test_apply_fill_increments_pnl_aggregate() -> None:
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()
    mock_trading = _make_trading()

    fixed_date = date(2025, 1, 6)
    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=_make_factory(), clock=clock
    )

    fill = _make_fill(avg_price=150.0, qty=10)
    await accountant.apply_fill(fill, Side.BUY, "INFY", "EQUITY")

    # BUY: sign = -1 → -150.0 * 10
    mock_trading.increment_pnl_aggregate.assert_awaited_once_with(
        fixed_date, pytest.approx(-1500.0)
    )


async def test_apply_fill_sell_increases_pnl() -> None:
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()
    mock_trading = _make_trading()

    fixed_date = date(2025, 1, 6)
    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=_make_factory(), clock=clock
    )

    fill = _make_fill(avg_price=100.0, qty=10)
    await accountant.apply_fill(fill, Side.SELL, "INFY", "EQUITY")

    mock_trading.increment_pnl_aggregate.assert_awaited_once_with(
        fixed_date, pytest.approx(1000.0)
    )


async def test_apply_fill_invalidates_api_cache() -> None:
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()

    mock_api = MagicMock()
    mock_api.invalidate_pnl = AsyncMock()
    mock_factory = MagicMock(spec=CacherFactory)
    mock_factory.api.return_value = mock_api

    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=_make_trading(), factory=mock_factory, clock=clock
    )

    fill = _make_fill()
    await accountant.apply_fill(fill, Side.BUY, "INFY", "EQUITY")

    mock_api.invalidate_pnl.assert_called_once_with(date(2025, 1, 6))


async def test_apply_fill_sequencing() -> None:
    """DB position update must fire before the PnL aggregate and API cache ops."""
    call_order: list[str] = []

    mock_position = MagicMock(spec=AbstractPositionStore)

    async def _record_position(*a, **kw) -> None:
        call_order.append("db")

    mock_position.update_position = _record_position

    mock_trading = MagicMock(spec=AbstractTradingStore)

    async def _record_pnl(*a, **kw) -> None:
        call_order.append("pnl")

    mock_trading.increment_pnl_aggregate = _record_pnl

    mock_api = MagicMock()

    async def _record_api(*a, **kw) -> None:
        call_order.append("api")

    mock_api.invalidate_pnl = _record_api

    mock_factory = MagicMock(spec=CacherFactory)
    mock_factory.api.return_value = mock_api

    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=mock_factory, clock=clock
    )

    await accountant.apply_fill(_make_fill(), Side.BUY, "INFY", "EQUITY")

    assert call_order == ["db", "pnl", "api"]
