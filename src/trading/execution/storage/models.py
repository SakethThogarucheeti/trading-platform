from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from trading.core.db_registry import shared_registry

if TYPE_CHECKING:
    from trading.strategy.storage.models import Signal

# Order.signal/Signal.orders below are string-based relationships resolved lazily
# against the shared registry -- they require trading.strategy.storage.models to
# have been imported by the time SQLAlchemy configures mappers (first DB use).
# trading.app.database imports every per-module Base at module load, which is the
# one chokepoint all real DB access already goes through.


class Base(DeclarativeBase):
    registry = shared_registry
    metadata = shared_registry.metadata


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kite_order_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    status: Mapped[str] = mapped_column(String)
    qty: Mapped[int]
    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    # Mirrors core.models.Order.client_tag (trading-platform#31) -- same
    # duplicated-column pattern #35 tracks for this whole class, following it
    # here rather than deviating, since TradingStore's own queries live in
    # this module and need to filter/read this column.
    client_tag: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # Denormalized off the originating Signal at Order-creation time
    # (trading-platform#83) so FillHandler can recover the owning algo without
    # a cross-module join between this module's and strategy.storage.models's
    # independent DeclarativeBase registries (see trading-platform#35).
    # Nullable: only orders placed after this column existed have one.
    algo_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    signal: Mapped[Signal] = relationship("Signal", back_populates="orders")


class Position(Base):
    __tablename__ = "positions"

    # Composite PK: (INFY, EQUITY, momentum) and (INFY, EQUITY, mean_reversion)
    # can coexist as independent rows (trading-platform#83) -- algo_name is
    # never NULL (Postgres disallows NULL in a composite PK column); callers
    # without a real algo_name use the "UNKNOWN" sentinel.
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_type: Mapped[str] = mapped_column(String, primary_key=True)
    algo_name: Mapped[str] = mapped_column(String, primary_key=True)
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
