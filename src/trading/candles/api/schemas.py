from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from trading.core.schemas import InstrumentType


class CandleEvent(BaseModel):
    symbol: str
    instrument_type: InstrumentType
    interval: str  # e.g. "1min", "5min"
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    timestamp: datetime  # bar-close timestamp
    tick_log_id: int  # copied from the tick that triggered bar close
