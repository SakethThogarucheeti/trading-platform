from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading.core.schemas import Side


@dataclass
class PositionState:
    net_qty: int
    avg_price: Decimal


class PositionLedger:
    @staticmethod
    def apply_fill(
        current: PositionState | None,
        fill_qty: int,
        fill_price: Decimal,
        side: Side,
    ) -> PositionState:
        if current is None:
            net_qty = fill_qty if side == Side.BUY else -fill_qty
            return PositionState(net_qty=net_qty, avg_price=fill_price)

        prev_qty = current.net_qty
        prev_price = current.avg_price
        signed_fill = fill_qty if side == Side.BUY else -fill_qty
        new_qty = prev_qty + signed_fill

        if new_qty == 0:
            # Flat: avg_price is unused until the next fill re-opens a
            # position. Kept per-side (BUY covering a short -> latest fill
            # price; SELL closing a long -> unchanged) for continuity with
            # existing behavior — the value is inert either way at qty 0.
            new_price = fill_price if side == Side.BUY else prev_price
        elif prev_qty == 0 or (prev_qty > 0) == (signed_fill > 0):
            # Opening from flat, or extending an existing position (long or
            # short) in the same direction: size-weighted average cost
            # basis. abs() makes this symmetric for shorts — previously
            # only the BUY side weight-averaged; an extending SELL on an
            # existing short discarded the prior cost basis and reset to
            # just the latest fill price, corrupting the short's avg_price.
            new_price = (prev_price * abs(prev_qty) + fill_price * fill_qty) / abs(new_qty)
        elif (prev_qty > 0) == (new_qty > 0):
            # Reducing an existing position without flipping sides: the
            # remaining shares keep their original cost basis.
            new_price = prev_price
        else:
            # Crossed through zero to the opposite side: a fresh position
            # at the fill price. Previously a BUY that overshot a short
            # (e.g. short 3, buy 10) incorrectly blended the closed short's
            # cost basis into the new long's average price via the
            # weighted-avg formula above instead of resetting here.
            new_price = fill_price

        return PositionState(net_qty=new_qty, avg_price=new_price)
