from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from trading.strategy.api.schemas import SignalEvent


class RiskContext(BaseModel):
    """Immutable snapshot of all inputs the gate chain needs — pre-fetched before any gate runs."""

    now: datetime
    today: date
    equity: float
    max_daily_loss_pct: float
    risk_per_trade_pct: float
    cutoff: time
    realized_pnl: float
    position: Any

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


@runtime_checkable
class RiskGate(Protocol):
    """Returns a rejection code string, or None if the signal may pass."""

    async def check(self, event: SignalEvent, ctx: RiskContext) -> str | None: ...


@runtime_checkable
class RiskSizer(Protocol):
    """Returns the order quantity (≥ 1), or 0 to reject."""

    def size(self, event: SignalEvent, ctx: RiskContext) -> int: ...
