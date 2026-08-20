"""
PositionStore.update_position() — real DB persistence for the ledger fix.

Regression test for a bug found in production: PositionLedger.apply_fill()
discarded the prior cost basis when a SELL extended an existing short
(reset to the latest fill price instead of weight-averaging), and blended
a closed short's cost basis into a new long's average price when a BUY
overshot a short instead of resetting at the crossing fill price.

The ledger math itself already has thorough pure-function unit tests
(tst/unit/execution/test_position_ledger.py) — this exercises the same
scenarios through PositionStore's real DB round-trip (SELECT ... FOR
UPDATE, persisted Decimal columns) since that's the code path the bug
actually shipped through.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading.core.schemas import FillEvent, Side
from trading.execution.storage.store import PositionStore


def _fill(price: float, qty: int) -> FillEvent:
    return FillEvent(
        kite_order_id=f"TEST_{price}_{qty}",
        avg_price=price,
        filled_qty=qty,
        timestamp=datetime.now(UTC),
    )


async def test_short_extension_weight_averages_cost_basis(engine, session_factory):
    store = PositionStore(session_factory)

    # Open short: sell 3 @ 100
    await store.update_position(_fill(100.0, 3), Side.SELL, "INFY", "EQUITY")
    pos = await store.get_position("INFY", "EQUITY")
    assert pos is not None
    assert pos.net_qty == -3
    assert pos.avg_price == Decimal("100")

    # Extend the short: sell 3 more @ 106 — must weight-average, not reset
    await store.update_position(_fill(106.0, 3), Side.SELL, "INFY", "EQUITY")
    pos = await store.get_position("INFY", "EQUITY")
    assert pos is not None
    assert pos.net_qty == -6
    assert pos.avg_price == Decimal("103")  # (100*3 + 106*3) / 6


async def test_buy_overshoot_on_short_flips_at_fill_price_not_blended(engine, session_factory):
    store = PositionStore(session_factory)

    # Open short: sell 3 @ 100
    await store.update_position(_fill(100.0, 3), Side.SELL, "INFY", "EQUITY")

    # Overshoot cover: buy 10 @ 110 — closes the short and opens a long of 7
    # at the fill price, not blended with the short's 100 cost basis.
    await store.update_position(_fill(110.0, 10), Side.BUY, "INFY", "EQUITY")
    pos = await store.get_position("INFY", "EQUITY")
    assert pos is not None
    assert pos.net_qty == 7
    assert pos.avg_price == Decimal("110")


async def test_reducing_without_flip_keeps_original_cost_basis(engine, session_factory):
    store = PositionStore(session_factory)

    # Open long: buy 10 @ 100
    await store.update_position(_fill(100.0, 10), Side.BUY, "INFY", "EQUITY")

    # Partial sell, no flip: sell 4 @ 120 — remaining 6 shares keep cost basis 100
    await store.update_position(_fill(120.0, 4), Side.SELL, "INFY", "EQUITY")
    pos = await store.get_position("INFY", "EQUITY")
    assert pos is not None
    assert pos.net_qty == 6
    assert pos.avg_price == Decimal("100")
