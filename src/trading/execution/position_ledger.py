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
        """
        Compute the new position state after a fill.

        BUY: increases qty, recomputes weighted-average price.
        SELL: decreases qty; avg_price unchanged when reducing a long,
              reset to fill_price when crossing into a short.
        """
        if current is None:
            net_qty = fill_qty if side == Side.BUY else -fill_qty
            return PositionState(net_qty=net_qty, avg_price=fill_price)

        prev_qty = current.net_qty
        prev_price = current.avg_price

        if side == Side.BUY:
            new_qty = prev_qty + fill_qty
            new_price = (
                (prev_price * prev_qty + fill_price * fill_qty) / new_qty
                if new_qty != 0
                else fill_price
            )
        else:
            new_qty = prev_qty - fill_qty
            new_price = fill_price if new_qty < 0 else prev_price

        return PositionState(net_qty=new_qty, avg_price=new_price)
