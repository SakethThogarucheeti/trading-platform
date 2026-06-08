from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Candle(Base):
    """
    Persisted OHLCV bar — one row per (symbol, interval, bar-close timestamp).
    """

    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    interval: Mapped[str] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    high: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    low: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    close: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    volume: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (
        UniqueConstraint("symbol", "interval", "ts", name="uq_candle_symbol_interval_ts"),
        Index("ix_candle_symbol_interval_ts", "symbol", "interval", "ts"),
    )


class Instrument(Base):
    __tablename__ = "instruments"

    token: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    exchange: Mapped[str] = mapped_column(String)
    instrument_type: Mapped[str] = mapped_column(String)

    underlying: Mapped[str | None] = mapped_column(String, nullable=True)
    expiry: Mapped[date | None] = mapped_column(nullable=True)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(2), nullable=True)
    lot_size: Mapped[int | None] = mapped_column(nullable=True)
