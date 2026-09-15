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


def _make_fill(
    avg_price: float = 100.0, qty: int = 10, kite_order_id: str = "KITE_001"
) -> FillEvent:
    return FillEvent(
        kite_order_id=kite_order_id,
        avg_price=avg_price,
        filled_qty=qty,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )


_UTC_ZONE = ZoneInfo("UTC")


class _FixedClock(Clock):
    def __init__(self, dt: datetime, tz: ZoneInfo = _UTC_ZONE) -> None:
        self._dt = dt
        self._tz = tz

    @property
    def tz(self) -> ZoneInfo:
        return self._tz

    def now(self) -> datetime:
        return self._dt


def _make_factory() -> CacherFactory:
    return CacherFactory(ValueCache(), SYSTEM_CLOCK)


def _make_trading(fills: list[tuple[str, int, float]] | None = None) -> AbstractTradingStore:
    mock = MagicMock(spec=AbstractTradingStore)
    mock.increment_pnl_aggregate = AsyncMock()
    mock.get_filled_fills = AsyncMock(return_value=fills or [])
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


async def test_apply_fill_opening_buy_realizes_zero_pnl() -> None:
    """An opening fill with nothing to match against realizes no P&L yet —
    it only becomes realized once a later fill closes against it (see
    test_apply_fill_matches_fifo_on_close below)."""
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

    mock_trading.increment_pnl_aggregate.assert_awaited_once_with(fixed_date, pytest.approx(0.0))


async def test_apply_fill_uses_ist_calendar_day_not_utc(
) -> None:
    """trading-platform#90: apply_fill's for_date key must be the IST calendar day
    (clock.today()), not the UTC calendar day (clock.now().date()) -- these differ
    for any timestamp between 00:00 and 05:30 IST, which is still 'yesterday' in UTC.
    A regression here would silently attribute realized P&L to the wrong trading day
    in the aggregate DailyLossGate reads from."""
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()
    mock_trading = _make_trading()

    # 2025-01-07 01:00 IST == 2025-01-06 19:30 UTC -- same instant, different calendar day.
    clock = _FixedClock(
        datetime(2025, 1, 6, 19, 30, tzinfo=UTC), tz=ZoneInfo("Asia/Kolkata")
    )
    assert clock.now().date() == date(2025, 1, 6)
    assert clock.today() == date(2025, 1, 7)

    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=_make_factory(), clock=clock
    )

    fill = _make_fill(avg_price=150.0, qty=10)
    await accountant.apply_fill(fill, Side.BUY, "INFY", "EQUITY")

    mock_trading.increment_pnl_aggregate.assert_awaited_once_with(
        date(2025, 1, 7), pytest.approx(0.0)
    )


async def test_apply_fill_opening_sell_realizes_zero_pnl() -> None:
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

    mock_trading.increment_pnl_aggregate.assert_awaited_once_with(fixed_date, pytest.approx(0.0))


async def test_apply_fill_matches_fifo_on_close() -> None:
    """BUY 10 @ 100 then SELL 10 @ 150 realizes (150-100)*10 = 500 on the closing fill."""
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()
    mock_trading = _make_trading()

    fixed_date = date(2025, 1, 6)
    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=_make_factory(), clock=clock
    )

    await accountant.apply_fill(
        _make_fill(avg_price=100.0, qty=10, kite_order_id="KITE_OPEN"), Side.BUY, "INFY", "EQUITY"
    )
    await accountant.apply_fill(
        _make_fill(avg_price=150.0, qty=10, kite_order_id="KITE_CLOSE"),
        Side.SELL,
        "INFY",
        "EQUITY",
    )

    assert mock_trading.increment_pnl_aggregate.await_args_list[0].args == (
        fixed_date,
        pytest.approx(0.0),
    )
    assert mock_trading.increment_pnl_aggregate.await_args_list[1].args == (
        fixed_date,
        pytest.approx(500.0),
    )


async def test_apply_fill_partial_close_matches_only_closed_qty() -> None:
    """BUY 10 @ 100, then SELL 4 @ 150 realizes (150-100)*4 = 200, leaving 6 long open."""
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()
    mock_trading = _make_trading()

    fixed_date = date(2025, 1, 6)
    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=_make_factory(), clock=clock
    )

    await accountant.apply_fill(
        _make_fill(avg_price=100.0, qty=10, kite_order_id="KITE_OPEN"), Side.BUY, "INFY", "EQUITY"
    )
    await accountant.apply_fill(
        _make_fill(avg_price=150.0, qty=4, kite_order_id="KITE_PARTIAL"),
        Side.SELL,
        "INFY",
        "EQUITY",
    )

    assert mock_trading.increment_pnl_aggregate.await_args_list[1].args == (
        fixed_date,
        pytest.approx(200.0),
    )


async def test_apply_fill_hydrates_queue_from_persisted_fills() -> None:
    """Simulates a process restart: the symbol has no in-memory queue state yet,
    but today's opening fill is already persisted (as get_filled_fills would report
    it, having been marked FILLED before apply_fill runs) — the closing fill must
    still match against it via a fresh hydration read, not treat it as a fresh open."""
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()
    mock_trading = _make_trading(fills=[("BUY", 10, 100.0)])

    fixed_date = date(2025, 1, 6)
    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=_make_factory(), clock=clock
    )

    fill = _make_fill(avg_price=150.0, qty=10, kite_order_id="KITE_CLOSE")
    await accountant.apply_fill(fill, Side.SELL, "INFY", "EQUITY")

    mock_trading.get_filled_fills.assert_awaited_once_with(
        fixed_date, "INFY", exclude_kite_order_id="KITE_CLOSE"
    )
    mock_trading.increment_pnl_aggregate.assert_awaited_once_with(
        fixed_date, pytest.approx(500.0)
    )


async def test_apply_fill_does_not_rehydrate_once_symbol_is_cached() -> None:
    """After the first fill for a symbol this process, later fills for the same
    symbol/day must not re-query get_filled_fills — the in-memory queue is reused."""
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()
    mock_trading = _make_trading()

    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=_make_factory(), clock=clock
    )

    await accountant.apply_fill(
        _make_fill(avg_price=100.0, qty=10, kite_order_id="KITE_1"), Side.BUY, "INFY", "EQUITY"
    )
    await accountant.apply_fill(
        _make_fill(avg_price=110.0, qty=5, kite_order_id="KITE_2"), Side.BUY, "INFY", "EQUITY"
    )

    assert mock_trading.get_filled_fills.await_count == 1


async def test_apply_fill_symbols_are_tracked_independently() -> None:
    """A closing fill for one symbol must not match against another symbol's queue."""
    mock_position = MagicMock(spec=AbstractPositionStore)
    mock_position.update_position = AsyncMock()
    mock_trading = _make_trading()

    clock = _FixedClock(datetime(2025, 1, 6, 9, 15, tzinfo=UTC))
    accountant = PositionAccountant(
        position=mock_position, trading=mock_trading, factory=_make_factory(), clock=clock
    )

    await accountant.apply_fill(
        _make_fill(avg_price=100.0, qty=10, kite_order_id="KITE_INFY"),
        Side.BUY,
        "INFY",
        "EQUITY",
    )
    await accountant.apply_fill(
        _make_fill(avg_price=200.0, qty=10, kite_order_id="KITE_TCS"),
        Side.SELL,
        "TCS",
        "EQUITY",
    )

    fixed_date = date(2025, 1, 6)
    # Both are opening fills for their respective symbols — neither matches the other.
    assert mock_trading.increment_pnl_aggregate.await_args_list[0].args == (
        fixed_date,
        pytest.approx(0.0),
    )
    assert mock_trading.increment_pnl_aggregate.await_args_list[1].args == (
        fixed_date,
        pytest.approx(0.0),
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
    mock_trading.get_filled_fills = AsyncMock(return_value=[])

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
