from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.schemas import OrderStatus, Side
from trading.execution.api.schemas import FillEvent, ValidatedOrderEvent
from trading.execution.service.ledger import PositionLedger, PositionState
from trading.execution.storage.models import Order, Position, StrategyAggregate
from trading.strategy.storage.models import Signal

_PNL_METRIC = "realized_pnl"


class NotFoundError(Exception):
    """Raised when a required DB row is absent."""


class TradingStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def save_signal(self, event: ValidatedOrderEvent) -> Signal:
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

    async def get_daily_realized_pnl(self, for_date: date) -> float:
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

    async def increment_pnl_aggregate(
        self, for_date: date, delta: float, algo_name: str = "ALL", symbol: str = "ALL"
    ) -> None:
        """
        Atomically add *delta* to the running realized-PnL total for the day.

        Postgres-only replacement for the old Redis/in-memory PnL cache: reads
        the row with SELECT ... FOR UPDATE inside a transaction (same pattern
        as PositionStore.update_position) so concurrent fills from different
        worker processes can never lose an update the way the old per-process
        in-memory cache silently did.
        """
        async with self._sf() as session:
            async with session.begin():
                result = await session.execute(
                    select(StrategyAggregate)
                    .where(
                        StrategyAggregate.metric == _PNL_METRIC,
                        StrategyAggregate.for_date == for_date,
                        StrategyAggregate.algo_name == algo_name,
                        StrategyAggregate.symbol == symbol,
                    )
                    .with_for_update()
                )
                row = result.scalar_one_or_none()
                if row is None:
                    session.add(
                        StrategyAggregate(
                            metric=_PNL_METRIC,
                            for_date=for_date,
                            algo_name=algo_name,
                            symbol=symbol,
                            value=Decimal(str(delta)),
                            updated_at=datetime.now(UTC),
                        )
                    )
                else:
                    row.value = row.value + Decimal(str(delta))
                    row.updated_at = datetime.now(UTC)

    async def get_pnl_aggregate(
        self, for_date: date, algo_name: str = "ALL", symbol: str = "ALL"
    ) -> float:
        """Read the running realized-PnL total written by increment_pnl_aggregate."""
        async with self._sf() as session:
            result = await session.execute(
                select(StrategyAggregate.value).where(
                    StrategyAggregate.metric == _PNL_METRIC,
                    StrategyAggregate.for_date == for_date,
                    StrategyAggregate.algo_name == algo_name,
                    StrategyAggregate.symbol == symbol,
                )
            )
            value = result.scalar_one_or_none()
            return float(value) if value is not None else 0.0

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


class PositionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_position(self, symbol: str, instrument_type: str) -> Position | None:
        async with self._sf() as session:
            return await session.get(
                Position, {"symbol": symbol, "instrument_type": instrument_type}
            )

    async def update_position(
        self, fill: FillEvent, side: Side, symbol: str, instrument_type: str
    ) -> None:
        async with self._sf() as session:
            async with session.begin():
                result = await session.execute(
                    select(Position)
                    .where(
                        Position.symbol == symbol,
                        Position.instrument_type == instrument_type,
                    )
                    .with_for_update()
                )
                position = result.scalar_one_or_none()
                current = (
                    PositionState(net_qty=position.net_qty, avg_price=position.avg_price)
                    if position is not None
                    else None
                )
                new_state = PositionLedger.apply_fill(
                    current=current,
                    fill_qty=fill.filled_qty,
                    fill_price=Decimal(str(fill.avg_price)),
                    side=side,
                )
                if position is None:
                    session.add(
                        Position(
                            symbol=symbol,
                            instrument_type=instrument_type,
                            net_qty=new_state.net_qty,
                            avg_price=new_state.avg_price,
                            updated_at=datetime.now(UTC),
                        )
                    )
                else:
                    position.net_qty = new_state.net_qty
                    position.avg_price = new_state.avg_price
                    position.updated_at = datetime.now(UTC)
