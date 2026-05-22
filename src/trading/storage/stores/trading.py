from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.models import Order, Position, Signal
from trading.core.schemas import FillEvent, OrderStatus, Side, SignalEvent


class NotFoundError(Exception):
    """Raised when a required DB row is absent."""


class AbstractTradingStore(ABC):
    @abstractmethod
    async def save_signal(self, event: SignalEvent) -> Signal: ...

    @abstractmethod
    async def save_order(self, order: Order) -> None: ...

    @abstractmethod
    async def get_order_by_kite_id(self, kite_order_id: str) -> Order | None: ...

    @abstractmethod
    async def update_order_status(
        self, kite_order_id: str, status: OrderStatus, avg_price: float = 0
    ) -> None: ...

    @abstractmethod
    async def get_position(self, symbol: str, instrument_type: str) -> Position | None: ...

    @abstractmethod
    async def update_position(
        self, fill: FillEvent, side: Side, symbol: str, instrument_type: str
    ) -> None: ...

    @abstractmethod
    async def get_daily_realized_pnl(self, for_date: date) -> float: ...

    @abstractmethod
    async def save_broker_token(self, broker: str, token: str, secret_key: str) -> None: ...

    @abstractmethod
    async def get_broker_token(self, broker: str, secret_key: str) -> str | None: ...


class TradingStore(AbstractTradingStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def save_signal(self, event: SignalEvent) -> Signal:
        signal = Signal(
            id=event.signal_id,
            strategy_id=event.strategy_id,
            algo_name=event.algo_name,
            symbol=event.symbol,
            instrument_type=event.instrument_type.value,
            side=event.side.value,
            signal_type=event.signal_type.value,
            stop_distance=Decimal(str(event.stop_distance)),
            created_at=event.timestamp,
        )
        async with self._sf() as session:
            async with session.begin():
                session.add(signal)
        return signal

    async def save_order(self, order: Order) -> None:
        async with self._sf() as session:
            async with session.begin():
                session.add(order)

    async def get_order_by_kite_id(self, kite_order_id: str) -> Order | None:
        async with self._sf() as session:
            result = await session.execute(
                select(Order).where(Order.kite_order_id == kite_order_id)
            )
            return result.scalar_one_or_none()

    async def update_order_status(
        self, kite_order_id: str, status: OrderStatus, avg_price: float = 0
    ) -> None:
        async with self._sf() as session:
            async with session.begin():
                result = await session.execute(
                    select(Order).where(Order.kite_order_id == kite_order_id)
                )
                order = result.scalar_one_or_none()
                if order is None:
                    raise NotFoundError(f"Order not found: {kite_order_id!r}")
                order.status = status.value
                order.avg_price = Decimal(str(avg_price))

    async def get_position(self, symbol: str, instrument_type: str) -> Position | None:
        async with self._sf() as session:
            return await session.get(Position, {"symbol": symbol, "instrument_type": instrument_type})

    async def update_position(
        self, fill: FillEvent, side: Side, symbol: str, instrument_type: str
    ) -> None:
        """
        Atomically update a position after a fill.

        Uses SELECT … FOR UPDATE so concurrent fills don't race.

        Position arithmetic:
          BUY fill  → net_qty += filled_qty, avg_price recomputed (weighted)
          SELL fill → net_qty -= filled_qty, avg_price unchanged when closing
        """
        async with self._sf() as session:
            async with session.begin():
                # SQLite doesn't support FOR UPDATE; use plain SELECT in tests.
                result = await session.execute(
                    select(Position)
                    .where(
                        Position.symbol == symbol,
                        Position.instrument_type == instrument_type,
                    )
                    .with_for_update()
                )
                position = result.scalar_one_or_none()
                fill_price = Decimal(str(fill.avg_price))
                fill_qty = fill.filled_qty

                if position is None:
                    net_qty = fill_qty if side == Side.BUY else -fill_qty
                    session.add(
                        Position(
                            symbol=symbol,
                            instrument_type=instrument_type,
                            net_qty=net_qty,
                            avg_price=fill_price,
                            updated_at=datetime.now(UTC),
                        )
                    )
                else:
                    prev_qty = position.net_qty
                    prev_price = position.avg_price

                    if side == Side.BUY:
                        new_qty = prev_qty + fill_qty
                        if new_qty != 0:
                            position.avg_price = (
                                prev_price * prev_qty + fill_price * fill_qty
                            ) / new_qty
                        position.net_qty = new_qty
                    else:  # SELL
                        new_qty = prev_qty - fill_qty
                        position.net_qty = new_qty
                        # avg_price stays the same when reducing a long; when crossing
                        # zero (short) we record the fill price as the new avg
                        if new_qty < 0:
                            position.avg_price = fill_price

                    position.updated_at = datetime.now(UTC)

    async def get_daily_realized_pnl(self, for_date: date) -> float:
        """
        Sum up realized P&L from FILLED orders placed on *for_date*.

        Formula: sum(avg_price * qty * side_multiplier) where
          side_multiplier = +1 for SELL fills, -1 for BUY fills
        """
        start = datetime(for_date.year, for_date.month, for_date.day, tzinfo=UTC)
        end = datetime(for_date.year, for_date.month, for_date.day, 23, 59, 59, tzinfo=UTC)

        async with self._sf() as session:
            result = await session.execute(
                select(Order, Signal)
                .join(Signal, Order.signal_id == Signal.id)
                .where(
                    Order.status == OrderStatus.FILLED.value,
                    Order.created_at >= start,
                    Order.created_at <= end,
                )
            )
            pnl = 0.0
            for order, signal in result.all():
                sign = 1.0 if signal.side == Side.SELL.value else -1.0
                pnl += sign * float(order.avg_price) * order.qty
        return pnl

    async def save_broker_token(self, broker: str, token: str, secret_key: str) -> None:
        async with self._sf() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        INSERT INTO broker_tokens (broker, token_enc, updated_at)
                        VALUES (:broker, pgp_sym_encrypt(:token, :key), now())
                        ON CONFLICT (broker) DO UPDATE
                          SET token_enc = pgp_sym_encrypt(:token, :key),
                              updated_at = now()
                    """),
                    {"broker": broker, "token": token, "key": secret_key},
                )

    async def get_broker_token(self, broker: str, secret_key: str) -> str | None:
        async with self._sf() as session:
            result = await session.execute(
                text("""
                    SELECT pgp_sym_decrypt(token_enc::bytea, :key)
                    FROM broker_tokens
                    WHERE broker = :broker
                """),
                {"broker": broker, "key": secret_key},
            )
            row = result.scalar_one_or_none()
            return str(row) if row is not None else None
