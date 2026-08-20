"""Tests for execution/service/eod_square_off.py — square_off_open_positions.

Regression coverage for a bug where the EOD flat-out zeroed the Position row
directly (via PositionStore.update_position) without ever creating a Signal
or Order — the exit fill was invisible to /api/pnl and /api/pnl/by-algo, so
the dashboard's realized P&L silently excluded every day's final close-out.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from trading.core.schemas import FillEvent, Side, SignalType
from trading.execution.service.eod_square_off import (
    EOD_SQUARE_OFF_NAME,
    square_off_open_positions,
)
from trading.execution.service.position_accountant import PositionAccountant


def _make_position(symbol: str, instrument_type: str, net_qty: int, avg_price: float) -> MagicMock:
    pos = MagicMock()
    pos.symbol = symbol
    pos.instrument_type = instrument_type
    pos.net_qty = net_qty
    pos.avg_price = Decimal(str(avg_price))
    return pos


def _make_trading(positions: list[MagicMock]) -> MagicMock:
    """A TradingStore stand-in whose _sf() session yields `positions` for the
    open-position query, plus AsyncMock save_signal/save_order."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = positions

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def sf():
        yield session

    trading = MagicMock()
    trading._sf = sf
    trading.save_signal = AsyncMock()
    trading.save_order = AsyncMock()
    return trading


def _make_clock(now: datetime) -> MagicMock:
    clock = MagicMock()
    clock.now.return_value = now
    clock.today.return_value = now.date()
    return clock


NOW = datetime(2026, 8, 20, 9, 59, 0, tzinfo=UTC)


async def test_no_open_positions_does_nothing() -> None:
    trading = _make_trading([])
    accountant = MagicMock(spec=PositionAccountant)
    accountant.apply_fill = AsyncMock()
    price_store = MagicMock()
    price_store.get.return_value = None

    await square_off_open_positions(trading, accountant, price_store, _make_clock(NOW))

    trading.save_signal.assert_not_called()
    trading.save_order.assert_not_called()
    accountant.apply_fill.assert_not_called()


async def test_long_position_squared_off_with_sell_and_full_audit_trail() -> None:
    pos = _make_position("INFY", "EQUITY", net_qty=3, avg_price=1133.2664)
    trading = _make_trading([pos])
    accountant = MagicMock(spec=PositionAccountant)
    accountant.apply_fill = AsyncMock()
    price_store = MagicMock()
    price_store.get.return_value = 1129.4

    await square_off_open_positions(trading, accountant, price_store, _make_clock(NOW))

    # Signal recorded with the sentinel algo/strategy name, not silently dropped or
    # misattributed to a real algo (e.g. via a null algo_name defaulting elsewhere).
    trading.save_signal.assert_called_once()
    event = trading.save_signal.call_args[0][0]
    assert event.algo_name == EOD_SQUARE_OFF_NAME
    assert event.strategy_id == EOD_SQUARE_OFF_NAME
    assert event.signal_type == SignalType.EXIT
    assert event.side == Side.SELL
    assert event.quantity == 3
    assert event.symbol == "INFY"

    # Order recorded FILLED at the current market price, linked to that signal.
    trading.save_order.assert_called_once()
    order = trading.save_order.call_args[0][0]
    assert order.signal_id == event.signal_id
    assert order.qty == 3
    assert order.avg_price == Decimal("1129.4")
    assert order.status == "FILLED"

    # Position update goes through the accountant (updates position, PnL cache,
    # and invalidates the PnL cache) rather than a bare position_store call.
    accountant.apply_fill.assert_called_once()
    fill: FillEvent = accountant.apply_fill.call_args[0][0]
    assert fill.avg_price == 1129.4
    assert fill.filled_qty == 3
    assert accountant.apply_fill.call_args[0][1] == Side.SELL
    assert accountant.apply_fill.call_args[0][2] == "INFY"
    assert accountant.apply_fill.call_args[0][3] == "EQUITY"


async def test_short_position_squared_off_with_buy() -> None:
    pos = _make_position("INFY", "EQUITY", net_qty=-2, avg_price=1130.0)
    trading = _make_trading([pos])
    accountant = MagicMock(spec=PositionAccountant)
    accountant.apply_fill = AsyncMock()
    price_store = MagicMock()
    price_store.get.return_value = 1131.5

    await square_off_open_positions(trading, accountant, price_store, _make_clock(NOW))

    event = trading.save_signal.call_args[0][0]
    assert event.side == Side.BUY
    assert event.quantity == 2
    assert accountant.apply_fill.call_args[0][1] == Side.BUY


async def test_falls_back_to_position_avg_price_when_price_store_has_none() -> None:
    pos = _make_position("INFY", "EQUITY", net_qty=3, avg_price=1133.2664)
    trading = _make_trading([pos])
    accountant = MagicMock(spec=PositionAccountant)
    accountant.apply_fill = AsyncMock()
    price_store = MagicMock()
    price_store.get.return_value = None

    await square_off_open_positions(trading, accountant, price_store, _make_clock(NOW))

    order = trading.save_order.call_args[0][0]
    assert order.avg_price == Decimal("1133.2664")


async def test_multiple_open_positions_all_squared_off() -> None:
    positions = [
        _make_position("INFY", "EQUITY", net_qty=3, avg_price=1133.0),
        _make_position("TCS", "EQUITY", net_qty=-5, avg_price=3500.0),
    ]
    trading = _make_trading(positions)
    accountant = MagicMock(spec=PositionAccountant)
    accountant.apply_fill = AsyncMock()
    price_store = MagicMock()
    price_store.get.side_effect = lambda symbol: {"INFY": 1129.0, "TCS": 3510.0}[symbol]

    await square_off_open_positions(trading, accountant, price_store, _make_clock(NOW))

    assert trading.save_signal.call_count == 2
    assert trading.save_order.call_count == 2
    assert accountant.apply_fill.call_count == 2


async def test_kite_order_id_disambiguates_by_instrument_type() -> None:
    """Two positions in the same symbol but different instrument types must not
    collide on the unique kite_order_id column."""
    positions = [
        _make_position("INFY", "EQUITY", net_qty=3, avg_price=1133.0),
        _make_position("INFY", "FUTURES", net_qty=1, avg_price=1140.0),
    ]
    trading = _make_trading(positions)
    accountant = MagicMock(spec=PositionAccountant)
    accountant.apply_fill = AsyncMock()
    price_store = MagicMock()
    price_store.get.return_value = 1129.0

    await square_off_open_positions(trading, accountant, price_store, _make_clock(NOW))

    order_ids = {call.args[0].kite_order_id for call in trading.save_order.call_args_list}
    assert len(order_ids) == 2
