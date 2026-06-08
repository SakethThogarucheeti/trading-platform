from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from trading.core.schemas import InstrumentType


class TickEvent(BaseModel):
    instrument_token: int
    instrument_type: InstrumentType
    last_price: float = Field(gt=0)
    volume: int = Field(ge=0)
    timestamp: datetime
    tick_log_id: int  # assigned by KiteIngestor after DB flush; propagated through pipeline
