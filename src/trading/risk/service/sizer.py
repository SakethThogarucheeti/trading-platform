from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.strategy.api.schemas import SignalEvent
    from trading.risk.service.policy import RiskContext

_MIN_STOP_FLOOR = 0.01
_MAX_NOTIONAL_PCT = 20.0


class VolatilitySizer:
    """ATR-based position sizer implementing the RiskSizer protocol."""

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
    if stop_distance <= 0 or equity <= 0 or risk_pct <= 0:
        return 0

    effective_stop = max(stop_distance, min_stop_floor)
    raw = math.floor((equity * risk_pct / 100.0) / effective_stop)

    if entry_price > 0:
        max_qty_notional = math.floor((equity * max_notional_pct / 100.0) / entry_price)
        raw = min(raw, max_qty_notional)

    if lot_size is not None and lot_size > 0:
        return max(0, (raw // lot_size) * lot_size)

    return max(0, raw)
