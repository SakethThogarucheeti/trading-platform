from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TickLog(Base):
    """
    Immutable record of every raw market tick received from the broker WebSocket.

    Every event downstream of a tick (candle, signal, order decision) carries
    the ``id`` of the originating TickLog row, forming a complete causal chain.
    """

    __tablename__ = "tick_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instrument_token: Mapped[int] = mapped_column(index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    instrument_type: Mapped[str] = mapped_column(String)
    last_price: Mapped[Decimal] = mapped_column()
    volume: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
