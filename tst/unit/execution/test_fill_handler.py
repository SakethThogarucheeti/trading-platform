"""Tests for execution/fill_handler.py — FillHandler"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from trading.core.schemas import FillEvent, Side
from trading.execution.service.fill_handler import FillHandler
from trading.execution.service.position_accountant import PositionAccountant
from trading.execution.api.interfaces import AbstractTradingStore
from trading.execution.storage.store import NotFoundError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fill_handler(
    trading: AbstractTradingStore | None = None,
    accountant: PositionAccountant | None = None,
) -> FillHandler:
    mock_trading = trading or MagicMock(spec=AbstractTradingStore)
    mock_trading.update_order_status = AsyncMock()
    mock_accountant = accountant or MagicMock(spec=PositionAccountant)
    mock_accountant.apply_fill = AsyncMock()
    return FillHandler(trading=mock_trading, accountant=mock_accountant)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_fill_marks_order_filled() -> None:
    mock_trading = MagicMock(spec=AbstractTradingStore)
    mock_trading.update_order_status = AsyncMock()
    mock_accountant = MagicMock(spec=PositionAccountant)
    mock_accountant.apply_fill = AsyncMock()

    handler = FillHandler(trading=mock_trading, accountant=mock_accountant)
    await handler.handle(
        kite_order_id="KITE_001",
        avg_price=150.0,
        filled_qty=10,
        symbol="INFY",
        instrument_type="EQUITY",
        side="BUY",
        tick_log_id=42,
    )

    from trading.core.schemas import OrderStatus
    mock_trading.update_order_status.assert_called_once_with("KITE_001", OrderStatus.FILLED, 150.0)


async def test_fill_calls_accountant_apply_fill() -> None:
    mock_trading = MagicMock(spec=AbstractTradingStore)
    mock_trading.update_order_status = AsyncMock()
    mock_accountant = MagicMock(spec=PositionAccountant)
    mock_accountant.apply_fill = AsyncMock()

    handler = FillHandler(trading=mock_trading, accountant=mock_accountant)
    await handler.handle(
        kite_order_id="KITE_001",
        avg_price=150.0,
        filled_qty=10,
        symbol="INFY",
        instrument_type="EQUITY",
        side="BUY",
        tick_log_id=42,
    )

    assert mock_accountant.apply_fill.call_count == 1
    call_args = mock_accountant.apply_fill.call_args
    fill_arg: FillEvent = call_args[0][0]
    assert fill_arg.kite_order_id == "KITE_001"
    assert fill_arg.avg_price == 150.0
    assert fill_arg.filled_qty == 10
    assert fill_arg.tick_log_id == 42
    assert call_args[0][1] == Side.BUY
    assert call_args[0][2] == "INFY"
    assert call_args[0][3] == "EQUITY"


async def test_fill_unknown_order_returns_early() -> None:
    """NotFoundError from trading store → accountant.apply_fill never called."""
    mock_trading = MagicMock(spec=AbstractTradingStore)
    mock_trading.update_order_status = AsyncMock(side_effect=NotFoundError("not found"))
    mock_accountant = MagicMock(spec=PositionAccountant)
    mock_accountant.apply_fill = AsyncMock()

    handler = FillHandler(trading=mock_trading, accountant=mock_accountant)
    await handler.handle(
        kite_order_id="GHOST",
        avg_price=100.0,
        filled_qty=5,
        symbol="INFY",
        instrument_type="EQUITY",
        side="BUY",
    )

    mock_accountant.apply_fill.assert_not_called()


async def test_fill_passes_tick_log_id() -> None:
    """tick_log_id is forwarded into the FillEvent passed to accountant."""
    mock_trading = MagicMock(spec=AbstractTradingStore)
    mock_trading.update_order_status = AsyncMock()
    mock_accountant = MagicMock(spec=PositionAccountant)
    mock_accountant.apply_fill = AsyncMock()

    handler = FillHandler(trading=mock_trading, accountant=mock_accountant)
    await handler.handle(
        kite_order_id="KITE_TL",
        avg_price=200.0,
        filled_qty=3,
        symbol="TCS",
        instrument_type="EQUITY",
        side="SELL",
        tick_log_id=99,
    )

    fill_arg: FillEvent = mock_accountant.apply_fill.call_args[0][0]
    assert fill_arg.tick_log_id == 99
