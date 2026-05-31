from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Candle(Base):
    """
    Persisted OHLCV bar — one row per (symbol, interval, bar-close timestamp).

    Populated by CandleRegistry on warmup and on every bar close. Used by the
    indicator library to compute values without holding an in-memory window.
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

    # F&O / crypto optional fields — NULL for equity
    underlying: Mapped[str | None] = mapped_column(String, nullable=True)
    expiry: Mapped[date | None] = mapped_column(nullable=True)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(2), nullable=True)  # CE | PE
    lot_size: Mapped[int | None] = mapped_column(nullable=True)


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

    orders: Mapped[list[Order]] = relationship(
        "Order", back_populates="signal", cascade="all, delete-orphan"
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kite_order_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"))
    status: Mapped[str] = mapped_column(String)
    qty: Mapped[int]
    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    signal: Mapped[Signal] = relationship("Signal", back_populates="orders")


class Position(Base):
    __tablename__ = "positions"

    # Composite PK: (INFY, EQUITY) and (INFY, FUTURES) can coexist
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_type: Mapped[str] = mapped_column(String, primary_key=True)
    net_qty: Mapped[int] = mapped_column(default=0)
    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BrokerToken(Base):
    """
    Encrypted broker access token stored in Postgres.

    ``token_enc`` holds the pgcrypto-encrypted ciphertext written via
    ``pgp_sym_encrypt(token, key)`` and read back with ``pgp_sym_decrypt``.
    The encryption key lives in the ``TOKEN_SECRET_KEY`` env var and never
    touches the DB.
    """

    __tablename__ = "broker_tokens"

    broker: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "zerodha"
    token_enc: Mapped[str] = mapped_column(String)                  # pgcrypto ciphertext
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    module: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    module: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    last_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    volume: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    decisions: Mapped[list[DecisionLog]] = relationship(
        "DecisionLog", back_populates="tick", cascade="all, delete-orphan"
    )


class AlgoConfig(Base):
    """
    Static configuration for an algo, stored in Postgres.

    ``params`` is a free-form JSON dict of strategy-specific hyperparameters
    (e.g. ``{"fast": 9, "slow": 21}``). Seeded from Settings on startup if the
    row does not already exist; manual DB edits survive restarts.
    """

    __tablename__ = "algo_configs"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String)
    warmup_candles: Mapped[int] = mapped_column(default=30)
    candle_intervals: Mapped[str] = mapped_column(String)  # JSON-encoded list
    equity: Mapped[float]
    enabled: Mapped[bool] = mapped_column(default=True)
    params: Mapped[str] = mapped_column(String, default="{}")  # JSON-encoded dict
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    state: Mapped[AlgoState | None] = relationship(
        "AlgoState", back_populates="config", uselist=False, cascade="all, delete-orphan"
    )


class AlgoState(Base):
    """
    Live runtime state for an algo, written by the bot after each candle.

    ``state`` is a free-form JSON dict — each algo/strategy writes whatever
    values are meaningful (bars_seen, warmup_complete, last_signal_at, current
    indicator values, etc.). The dashboard renders all keys as a live KV table.
    """

    __tablename__ = "algo_state"

    name: Mapped[str] = mapped_column(String, ForeignKey("algo_configs.name"), primary_key=True)
    state: Mapped[str] = mapped_column(String, default="{}")  # JSON-encoded dict
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    config: Mapped[AlgoConfig] = relationship("AlgoConfig", back_populates="state")


class IndicatorLog(Base):
    """
    Per-bar indicator values pushed by strategies via Strategy.chart().

    ``session_id`` mirrors DecisionLog: NULL = live trading, named string = backtest run.
    ``chart`` is a logical grouping (e.g. "price", "oscillators"); ``series`` is the
    individual line name (e.g. "ema_9", "atr_14").
    """

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
    """
    Audit record for every decision made in response to a tick.

    ``tick_log_id`` links every decision back to its originating tick.
    ``session_id`` identifies a backtest or Monte Carlo run (NULL = live trading).

    Steps:
    - CANDLE_EMITTED     — CandleAggregator closed a bar
    - SIGNAL_GENERATED   — AlgoRunner's strategy produced a signal
    - SIGNAL_ACCEPTED    — RiskController accepted and forwarded the signal
    - SIGNAL_REJECTED    — RiskController rejected the signal (reason in context)
    """

    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tick_log_id: Mapped[int] = mapped_column(ForeignKey("tick_logs.id"), index=True)
    step: Mapped[str] = mapped_column(String, index=True)
    algo_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    signal_id: Mapped[UUID | None] = mapped_column(nullable=True)
    context: Mapped[str] = mapped_column(String)  # JSON string
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    tick: Mapped[TickLog] = relationship("TickLog", back_populates="decisions")
