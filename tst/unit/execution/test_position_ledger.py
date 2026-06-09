"""Pure unit tests for execution/position_ledger.py — no DB, no async, no fixtures."""

from __future__ import annotations

from decimal import Decimal

from trading.core.schemas import Side
from trading.execution.service.ledger import PositionLedger, PositionState


def _state(qty: int, price: float) -> PositionState:
    return PositionState(net_qty=qty, avg_price=Decimal(str(price)))


def test_first_buy_creates_long_position() -> None:
    result = PositionLedger.apply_fill(None, fill_qty=10, fill_price=Decimal("100"), side=Side.BUY)
    assert result.net_qty == 10
    assert result.avg_price == Decimal("100")


def test_first_sell_creates_short_position() -> None:
    result = PositionLedger.apply_fill(None, fill_qty=5, fill_price=Decimal("200"), side=Side.SELL)
    assert result.net_qty == -5
    assert result.avg_price == Decimal("200")


def test_buy_adds_qty_and_recomputes_weighted_avg() -> None:
    current = _state(10, 100.0)
    result = PositionLedger.apply_fill(current, fill_qty=10, fill_price=Decimal("110"), side=Side.BUY)
    assert result.net_qty == 20
    assert result.avg_price == Decimal("105")


def test_sell_reduces_qty_avg_price_unchanged() -> None:
    current = _state(10, 100.0)
    result = PositionLedger.apply_fill(current, fill_qty=5, fill_price=Decimal("120"), side=Side.SELL)
    assert result.net_qty == 5
    assert result.avg_price == Decimal("100")  # unchanged when reducing a long


def test_sell_closes_long_to_zero_avg_price_unchanged() -> None:
    current = _state(10, 100.0)
    result = PositionLedger.apply_fill(current, fill_qty=10, fill_price=Decimal("120"), side=Side.SELL)
    assert result.net_qty == 0
    assert result.avg_price == Decimal("100")  # unchanged when qty reaches zero


def test_sell_crossing_into_short_resets_avg_price() -> None:
    current = _state(10, 100.0)
    result = PositionLedger.apply_fill(current, fill_qty=15, fill_price=Decimal("90"), side=Side.SELL)
    assert result.net_qty == -5
    assert result.avg_price == Decimal("90")  # reset to fill price when short


def test_buy_to_zero_qty_uses_fill_price() -> None:
    # Short position bought back to exactly zero — division guard triggers
    current = _state(-10, 95.0)
    result = PositionLedger.apply_fill(current, fill_qty=10, fill_price=Decimal("98"), side=Side.BUY)
    assert result.net_qty == 0
    assert result.avg_price == Decimal("98")  # fill_price used when new_qty == 0
