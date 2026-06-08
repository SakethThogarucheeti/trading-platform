from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from trading.risk.api.schemas import ValidatedOrderEvent  # noqa: F401 — re-exported


class FillEvent(BaseModel):
    kite_order_id: str
    avg_price: float = Field(gt=0)
    filled_qty: int = Field(gt=0)
    timestamp: datetime
    tick_log_id: int = 0
