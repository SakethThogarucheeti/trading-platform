from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from trading.core.schemas import InstrumentType, OrderType, Side
from trading.strategy.api.schemas import SignalEvent  # noqa: F401 — re-exported for consumers


class ValidatedOrderEvent(BaseModel):
    signal_id: UUID
    symbol: str
    instrument_type: InstrumentType
    side: Side
    quantity: int = Field(gt=0)
    order_type: OrderType
    limit_price: float | None = None
    tick_log_id: int

    @classmethod
    def from_signal_event(cls, event: SignalEvent, quantity: int) -> ValidatedOrderEvent:
        return cls(
            signal_id=event.signal_id,
            symbol=event.symbol,
            instrument_type=event.instrument_type,
            side=event.side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            limit_price=None,
            tick_log_id=event.tick_log_id,
        )
