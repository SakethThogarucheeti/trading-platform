from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String)
    algo_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String)
    instrument_type: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)
    signal_type: Mapped[str] = mapped_column(String)
    stop_distance: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    orders: Mapped[list] = relationship(
        "Order", back_populates="signal", cascade="all, delete-orphan",
        primaryjoin="Signal.id == foreign(Order.signal_id)",
    )


class AlgoConfig(Base):
    __tablename__ = "algo_configs"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String)
    warmup_candles: Mapped[int] = mapped_column(default=30)
    candle_intervals: Mapped[str] = mapped_column(String)
    equity: Mapped[float]
    enabled: Mapped[bool] = mapped_column(default=True)
    params: Mapped[str] = mapped_column(String, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    state: Mapped[AlgoState | None] = relationship(
        "AlgoState", back_populates="config", uselist=False, cascade="all, delete-orphan"
    )


class AlgoState(Base):
    __tablename__ = "algo_state"

    name: Mapped[str] = mapped_column(String, ForeignKey("algo_configs.name"), primary_key=True)
    state: Mapped[str] = mapped_column(String, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    config: Mapped[AlgoConfig] = relationship("AlgoConfig", back_populates="state")


class IndicatorLog(Base):
    __tablename__ = "indicator_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    algo_name: Mapped[str] = mapped_column(String, index=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    interval: Mapped[str] = mapped_column(String)
    chart: Mapped[str] = mapped_column(String)
    series: Mapped[str] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float]


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tick_log_id: Mapped[int] = mapped_column(index=True)
    step: Mapped[str] = mapped_column(String, index=True)
    algo_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    signal_id: Mapped[UUID | None] = mapped_column(nullable=True)
    context: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
