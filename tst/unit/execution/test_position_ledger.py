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


def test_sell_extends_short_recomputes_weighted_avg() -> None:
    # Regression: extending an existing short must weight-average against
    # the prior cost basis, not discard it and reset to the latest fill.
    current = _state(-5, 100.0)
    result = PositionLedger.apply_fill(current, fill_qty=3, fill_price=Decimal("110"), side=Side.SELL)
    assert result.net_qty == -8
    assert result.avg_price == Decimal("103.75")  # (100*5 + 110*3) / 8


def test_buy_covers_short_and_reduces_without_flipping() -> None:
    current = _state(-10, 95.0)
    result = PositionLedger.apply_fill(current, fill_qty=3, fill_price=Decimal("98"), side=Side.BUY)
    assert result.net_qty == -7
    assert result.avg_price == Decimal("95")  # remaining short keeps its original cost basis


def test_buy_overshoots_short_flips_to_long_at_fill_price() -> None:
    # Regression: a BUY that closes a short AND opens a long must not blend
    # the closed short's cost basis into the new long's avg_price.
    current = _state(-3, 100.0)
    result = PositionLedger.apply_fill(current, fill_qty=10, fill_price=Decimal("105"), side=Side.BUY)
    assert result.net_qty == 7
    assert result.avg_price == Decimal("105")  # fresh long position at the fill price
