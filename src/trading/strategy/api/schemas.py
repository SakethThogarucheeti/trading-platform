from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from trading.candles.api.schemas import CandleEvent  # noqa: F401 — re-exported for consumers
from trading.core.schemas import InstrumentType, OrderType, Side, SignalType


class SignalEvent(BaseModel):
    signal_id: UUID = Field(default_factory=uuid4)
    symbol: str
    instrument_type: InstrumentType
    side: Side
    strategy_id: str
    algo_name: str | None = None
    signal_type: SignalType
    stop_distance: float = Field(gt=0)
    entry_price: float = Field(default=0.0, ge=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tick_log_id: int

    @classmethod
    def from_signal(
        cls, signal: object, tick_log_id: int, algo_name: str | None = None
    ) -> SignalEvent:
        from trading.strategy.service.base import Signal

        s: Signal = signal  # type: ignore[assignment]
        return cls(
            signal_id=s.signal_id,
            symbol=s.symbol,
            instrument_type=s.instrument_type,
            side=s.side,
            strategy_id=s.strategy_id,
            algo_name=algo_name,
            signal_type=s.signal_type,
            stop_distance=s.stop_distance,
            entry_price=s.entry_price,
            timestamp=s.timestamp,
            tick_log_id=tick_log_id,
        )
