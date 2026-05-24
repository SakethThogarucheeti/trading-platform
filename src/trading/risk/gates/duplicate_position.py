from __future__ import annotations

from trading.core.schemas import Side, SignalEvent, SignalType
from trading.risk.policy import RiskContext

_ALREADY_IN_POSITION = "ALREADY_IN_POSITION"


class DuplicatePositionGate:
    """Rejects ENTRY signals that would double-up an existing open position."""

    async def check(self, event: SignalEvent, ctx: RiskContext) -> str | None:
        if event.signal_type != SignalType.ENTRY:
            return None
        pos = ctx.position
        if pos is None or pos.net_qty == 0:
            return None
        if (pos.net_qty > 0 and event.side == Side.BUY) or (
            pos.net_qty < 0 and event.side == Side.SELL
        ):
            return _ALREADY_IN_POSITION
        return None
