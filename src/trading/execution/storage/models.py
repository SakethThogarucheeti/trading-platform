from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kite_order_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    signal_id: Mapped[UUID] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String)
    qty: Mapped[int]
    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


class Position(Base):
    __tablename__ = "positions"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_type: Mapped[str] = mapped_column(String, primary_key=True)
    net_qty: Mapped[int] = mapped_column(default=0)
    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StrategyAggregate(Base):
    """
    Running-total aggregate store, keyed by (metric, date, algo, symbol).

    Postgres-only replacement for the old Redis/in-memory PnL cache: rows are
    updated atomically via UPSERT (see TradingStore.increment_pnl_aggregate)
    so the total stays correct across concurrent worker processes, unlike the
    per-process in-memory cache it replaces.

    Only "realized_pnl" with algo_name="ALL"/symbol="ALL" (a portfolio-wide
    total) is written today, but algo_name/symbol are real dimensions so a
    future per-algo or per-symbol aggregate doesn't need a migration.
    """

    __tablename__ = "strategy_aggregates"

    metric: Mapped[str] = mapped_column(String, primary_key=True)
    for_date: Mapped[date] = mapped_column(Date, primary_key=True)
    algo_name: Mapped[str] = mapped_column(String, primary_key=True, default="ALL")
    symbol: Mapped[str] = mapped_column(String, primary_key=True, default="ALL")
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )
