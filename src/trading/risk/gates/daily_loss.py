from __future__ import annotations

from trading.core.schemas import SignalEvent
from trading.risk.service.policy import RiskContext

_DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"


class DailyLossGate:
    """
    Rejects signals when today's realized PnL exceeds the configured max loss.

    Pass ``enabled=False`` (set by DI when paper_trading=True) to make this gate
    a pass-through without any conditional inside RiskFilter.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    async def check(self, event: SignalEvent, ctx: RiskContext) -> str | None:
        if not self._enabled:
            return None
        limit = ctx.equity * ctx.max_daily_loss_pct / 100.0
        if abs(ctx.realized_pnl) > limit:
            return _DAILY_LOSS_LIMIT
        return None
