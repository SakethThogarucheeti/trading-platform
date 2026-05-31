from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.core.schemas import SignalEvent
    from trading.risk.policy import RiskContext

# Smallest stop distance accepted — prevents astronomically large quantities
# when ATR is near zero (common on synthetic/low-volatility data).
_MIN_STOP_FLOOR = 0.01

# Hard cap: position notional ≤ this fraction of equity.
# At 20%: max 5 positions at full size, or one position at 20× the risk amount.
_MAX_NOTIONAL_PCT = 20.0


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
            entry_price=event.entry_price,
            lot_size=self._lot_size,
        )


def calculate_quantity(
    stop_distance: float,
    equity: float,
    risk_pct: float,
    entry_price: float = 0.0,
    lot_size: int | None = None,
    min_stop_floor: float = _MIN_STOP_FLOOR,
    max_notional_pct: float = _MAX_NOTIONAL_PCT,
) -> int:
    """
    Risk-based position sizer.

    qty = floor((equity * risk_pct / 100) / effective_stop)

    where effective_stop = max(stop_distance, min_stop_floor).

    When entry_price > 0, additionally caps qty so that the position
    notional (qty × entry_price) does not exceed max_notional_pct of equity.
    This prevents runaway quantities when ATR is tiny relative to price.

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
    entry_price:
        Indicative entry price. When > 0, enables the notional cap.
        Set to 0 when price is unknown (disables cap, min_stop_floor still applies).
    lot_size:
        Lot size for futures/options. ``None`` for equity (unit-traded).
    min_stop_floor:
        Minimum effective stop distance. Prevents division by near-zero ATR.
    max_notional_pct:
        Hard cap: qty × entry_price ≤ equity × max_notional_pct / 100.

    Returns
    -------
    int
        Number of shares / contracts to trade. Always ≥ 0.
    """
    if stop_distance <= 0 or equity <= 0 or risk_pct <= 0:
        return 0

    effective_stop = max(stop_distance, min_stop_floor)
    raw = math.floor((equity * risk_pct / 100.0) / effective_stop)

    # Notional cap: qty × price ≤ equity × max_notional_pct / 100
    if entry_price > 0:
        max_qty_notional = math.floor((equity * max_notional_pct / 100.0) / entry_price)
        raw = min(raw, max_qty_notional)

    if lot_size is not None and lot_size > 0:
        # Round down to nearest lot multiple
        return max(0, (raw // lot_size) * lot_size)

    return max(0, raw)
