from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from trading.strategy.base import Signal

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InstrumentType(StrEnum):
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"
    CRYPTO = "CRYPTO"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class SignalType(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PLACED = "PLACED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class OptionType(StrEnum):
    CE = "CE"
    PE = "PE"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL_M"


# ---------------------------------------------------------------------------
# Pipeline event models — defined here (single source of truth).
# Module api/schemas.py files re-export these rather than redefining them.
# ---------------------------------------------------------------------------


class TickEvent(BaseModel):
    instrument_token: int
    instrument_type: InstrumentType
    last_price: float = Field(gt=0)
    volume: int = Field(ge=0)
    timestamp: datetime
    tick_log_id: int


class CandleEvent(BaseModel):
    symbol: str
    instrument_type: InstrumentType
    interval: str
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    timestamp: datetime
    tick_log_id: int


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
        cls, signal: Signal, tick_log_id: int, algo_name: str | None = None
    ) -> SignalEvent:
        return cls(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            instrument_type=signal.instrument_type,
            side=signal.side,
            strategy_id=signal.strategy_id,
            algo_name=algo_name,
            signal_type=signal.signal_type,
            stop_distance=signal.stop_distance,
            entry_price=signal.entry_price,
            timestamp=signal.timestamp,
            tick_log_id=tick_log_id,
        )


class ValidatedOrderEvent(BaseModel):
    signal_id: UUID
    symbol: str
    instrument_type: InstrumentType
    side: Side
    quantity: int = Field(gt=0)
    order_type: OrderType
    limit_price: float | None = None
    tick_log_id: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Carried through from SignalEvent for audit/persistence
    strategy_id: str = ""
    algo_name: str | None = None
    signal_type: SignalType = SignalType.ENTRY
    stop_distance: float = 0.0

    @classmethod
    def from_signal_event(cls, event: SignalEvent, quantity: int) -> ValidatedOrderEvent:
        return cls(
            signal_id=event.signal_id,
            symbol=event.symbol,
            instrument_type=event.instrument_type,
            side=event.side,
            strategy_id=event.strategy_id,
            algo_name=event.algo_name,
            signal_type=event.signal_type,
            stop_distance=event.stop_distance,
            quantity=quantity,
            order_type=OrderType.MARKET,
            limit_price=None,
            tick_log_id=event.tick_log_id,
        )


class OrderEvent(BaseModel):
    signal_id: UUID
    kite_order_id: str
    status: OrderStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FillEvent(BaseModel):
    kite_order_id: str
    avg_price: float = Field(gt=0)
    filled_qty: int = Field(gt=0)
    timestamp: datetime
    tick_log_id: int = 0
