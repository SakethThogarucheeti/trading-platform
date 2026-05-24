from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.core.schemas import SignalEvent
    from trading.risk.policy import RiskContext


class VolatilitySizer:
    """
    ATR-based position sizer implementing the RiskSizer protocol.

    Wraps ``calculate_quantity`` with optional lot-size rounding.
    """

    def __init__(self, lot_size: int | None = None) -> None:
        self._lot_size = lot_size

    def size(self, event: SignalEvent, ctx: RiskContext) -> int:
        return calculate_quantity(
            stop_distance=event.stop_distance,
            equity=ctx.equity,
            risk_pct=ctx.risk_per_trade_pct,
            lot_size=self._lot_size,
        )


def calculate_quantity(
    stop_distance: float,
    equity: float,
    risk_pct: float,
    lot_size: int | None = None,
) -> int:
    """
    Risk-based position sizer.

    qty = floor((equity * risk_pct / 100) / stop_distance)

    For lot-traded instruments (futures, options) the result is rounded
    down to the nearest lot multiple. Returns 0 when the computed quantity
    is below one lot (or below 1 for non-lot instruments).

    Parameters
    ----------
    stop_distance:
        Distance from entry to stop-loss (ATR-based). Must be > 0.
    equity:
        Total account equity used to size the trade.
    risk_pct:
        Percentage of equity to risk per trade (e.g. 1.0 = 1 %).
    lot_size:
        Lot size for futures/options. ``None`` for equity (unit-traded).

    Returns
    -------
    int
        Number of shares / contracts to trade. Always ≥ 0.
    """
    if stop_distance <= 0 or equity <= 0 or risk_pct <= 0:
        return 0

    raw = math.floor((equity * risk_pct / 100.0) / stop_distance)

    if lot_size is not None and lot_size > 0:
        # Round down to nearest lot multiple
        return max(0, (raw // lot_size) * lot_size)

    return max(0, raw)
