from __future__ import annotations

import math


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
