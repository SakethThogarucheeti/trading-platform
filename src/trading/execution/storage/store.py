from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select, text
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

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """
        Open one session/transaction that callers can pass into the
        `*_in_session` variants below (and into PositionStore's, since both
        stores share the same session_factory in every DI wiring path) to
        make several writes atomic together -- e.g. FillHandler.handle's
        order-status + position + PnL-aggregate triad (trading-platform#91)
        and eod_square_off's locked re-read + apply (trading-platform#96).
        """
        async with self._sf() as session:
            async with session.begin():
                yield session

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
    ) -> bool:
        """
        Returns False (no-op) if the order is already FILLED -- guards against
        the same fill being applied twice, e.g. a webhook postback and
        OrderReconciler both resolving the same kite_order_id in the same poll
        window (trading-platform#31 review). `with_for_update()` serializes
        that race against a concurrent caller rather than just checking then
        writing. Any other current status may still transition to FILLED,
        including a reconciler-driven REJECTED-from-timeout correction.
        """
        async with self._sf() as session:
            async with session.begin():
                return await self.update_order_status_in_session(
                    session, kite_order_id, status, avg_price
                )

    async def update_order_status_in_session(
        self,
        session: AsyncSession,
        kite_order_id: str,
        status: OrderStatus,
        avg_price: float = 0,
    ) -> bool:
        """Same as update_order_status, but runs inside a caller-supplied,
        already-open transaction (see `transaction()`) instead of opening its
        own -- so it can commit atomically alongside other writes."""
        result = await session.execute(
            select(Order).where(Order.kite_order_id == kite_order_id).with_for_update()
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise NotFoundError(f"Order not found: {kite_order_id!r}")
        if order.status == OrderStatus.FILLED.value:
            return False
        order.status = status.value
        order.avg_price = Decimal(str(avg_price))
        return True

    async def get_unresolved_tagged_orders(self) -> list[tuple[Order, Signal]]:
        """
        Orders that still need broker-side reconciliation (trading-platform#31):
        either never made it past PENDING (process died before place_order
        returned), or were marked REJECTED from a placement timeout (synthetic
        `FAILED_` kite_order_id -- see OrderExecutor._place_with_broker). Only
        orders with a client_tag can be matched back to the broker's own order
        book by OrderReconciler.

        Joined with Signal so OrderReconciler can build a fill/rejection event
        from *our own* symbol/instrument_type/side rather than parsing Kite's
        order-book fields (tradingsymbol/transaction_type don't carry
        instrument_type at all).
        """
        async with self._sf() as session:
            result = await session.execute(
                select(Order, Signal)
                .join(Signal, Order.signal_id == Signal.id)
                .where(
                    Order.client_tag.is_not(None),
                    or_(
                        Order.status == OrderStatus.PENDING.value,
                        Order.kite_order_id.like("FAILED_%"),
                    ),
                )
            )
            return [(order, signal) for order, signal in result.all()]

    async def reconcile_order_id(self, order_id: UUID, kite_order_id: str) -> None:
        """Correct a row's kite_order_id once OrderReconciler matches it by client_tag."""
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(Order, order_id, with_for_update=True)
                if row is None:
                    raise NotFoundError(f"Order not found: {order_id!r}")
                row.kite_order_id = kite_order_id

    async def mark_order_terminal(self, order_id: UUID, status: OrderStatus) -> bool:
        """
        Directly correct a row's status when the broker reports a terminal
        non-fill outcome (REJECTED/CANCELLED) for a tag-matched order --
        no fill to apply, so this skips FillHandler/PositionAccountant entirely.

        Returns False (no-op) if the row is already FILLED -- a fill (from
        the webhook or this same reconciler's own COMPLETE branch) that
        lands first must not be clobbered back to a terminal non-fill status
        by stale/racing broker order-book data. Callers must check the
        return value before logging the requested status as applied.
        """
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(Order, order_id, with_for_update=True)
                if row is None:
                    raise NotFoundError(f"Order not found: {order_id!r}")
                if row.status == OrderStatus.FILLED.value:
                    return False
                row.status = status.value
                return True

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

    async def get_filled_fills(
        self, for_date: date, symbol: str, exclude_kite_order_id: str | None = None
    ) -> list[tuple[str, int, Decimal]]:
        """
        Return today's already-FILLED (side, qty, avg_price) fills for *symbol*,
        oldest first — used to hydrate an in-memory FIFO queue (e.g. on process
        restart) from what's already durably persisted, with no separate queue
        storage of its own.
        """
        async with self._sf() as session:
            return await self.get_filled_fills_in_session(
                session, for_date, symbol, exclude_kite_order_id
            )

    async def get_order_algo_name_in_session(
        self, session: AsyncSession, kite_order_id: str
    ) -> str | None:
        """
        The denormalized ``algo_name`` off the Order row identified by
        *kite_order_id* (trading-platform#83) -- lets FillHandler recover the
        owning algo without a cross-module join between this module's and
        strategy.storage.models's independent DeclarativeBase registries
        (see trading-platform#35). Returns None if the order has none (e.g.
        a test-only/manually-built event, or a row from before this column
        existed) -- callers apply their own non-null sentinel.
        """
        result = await session.execute(
            select(Order.algo_name).where(Order.kite_order_id == kite_order_id)
        )
        return result.scalar_one_or_none()

    async def get_filled_fills_in_session(
        self,
        session: AsyncSession,
        for_date: date,
        symbol: str,
        exclude_kite_order_id: str | None = None,
    ) -> list[tuple[str, int, Decimal]]:
        """Same as get_filled_fills, but runs on a caller-supplied session --
        used by PositionAccountant.apply_fill so this read stays on the same
        connection as the fill's writes rather than opening a second,
        interleaved session mid-transaction (trading-platform#91).

        Returns `avg_price` as the `Decimal` the DB column already stores
        (no `float()` cast) so FIFO-hydration feeds `match_against` exact
        values rather than reintroducing float error on read (#92)."""
        start = datetime(for_date.year, for_date.month, for_date.day, tzinfo=UTC)
        end = datetime(for_date.year, for_date.month, for_date.day, 23, 59, 59, tzinfo=UTC)
        query = (
            select(Order, Signal)
            .join(Signal, Order.signal_id == Signal.id)
            .where(
                Order.status == OrderStatus.FILLED.value,
                Signal.symbol == symbol,
                Order.created_at >= start,
                Order.created_at <= end,
            )
            .order_by(Order.created_at.asc())
        )
        if exclude_kite_order_id is not None:
            query = query.where(Order.kite_order_id != exclude_kite_order_id)
        result = await session.execute(query)
        return [(signal.side, order.qty, order.avg_price) for order, signal in result.all()]

    async def increment_pnl_aggregate(
        self,
        for_date: date,
        delta: float | Decimal,
        algo_name: str = "ALL",
        symbol: str = "ALL",
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
                await self.increment_pnl_aggregate_in_session(
                    session, for_date, delta, algo_name, symbol
                )

    async def increment_pnl_aggregate_in_session(
        self,
        session: AsyncSession,
        for_date: date,
        delta: float | Decimal,
        algo_name: str = "ALL",
        symbol: str = "ALL",
    ) -> None:
        """Same as increment_pnl_aggregate, but runs inside a caller-supplied,
        already-open transaction instead of opening its own.

        `delta` accepts `Decimal` directly (PositionAccountant.apply_fill
        now computes exact FIFO-matched P&L, #92) as well as `float` (other
        callers, e.g. tests seeding a PnL total) -- `Decimal(str(delta))`
        below round-trips a `Decimal` input exactly, so accepting both here
        costs no precision either way."""
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
        """
        The blended (symbol, instrument_type) exposure, aggregated across
        every algo's own row (trading-platform#83 widened the PK to include
        algo_name, so a single ``session.get()`` composite-key lookup no
        longer identifies one row). Used by call sites -- e.g. RiskFilter's
        no-algo_name fallback -- that intentionally want the shared blended
        view rather than one algo's own exposure (see ``get_algo_position``).

        net_qty sums directly; avg_price is weighted by each row's
        ``abs(net_qty)``, matching the weighted-average convention already
        used elsewhere in this codebase. Returns a transient (unpersisted)
        Position, not one of the underlying rows.
        """
        async with self._sf() as session:
            result = await session.execute(
                select(Position).where(
                    Position.symbol == symbol,
                    Position.instrument_type == instrument_type,
                )
            )
            rows = list(result.scalars().all())
            if not rows:
                return None
            total_qty = sum(row.net_qty for row in rows)
            weight = sum(abs(row.net_qty) for row in rows)
            avg_price = (
                sum(row.avg_price * abs(row.net_qty) for row in rows) / weight
                if weight > 0
                else rows[0].avg_price
            )
            latest_updated_at = max(row.updated_at for row in rows)
            return Position(
                symbol=symbol,
                instrument_type=instrument_type,
                algo_name="ALL",
                net_qty=total_qty,
                avg_price=avg_price,
                updated_at=latest_updated_at,
            )

    async def get_algo_position(
        self, symbol: str, instrument_type: str, algo_name: str
    ) -> PositionState | None:
        """
        This algo's own exposure, replayed from its own FILLED orders/signals --
        not the shared ``positions`` row, which is blended across every algo that
        trades this (symbol, instrument_type) until that table's PK is widened
        with ``algo_name`` (trading-platform#8). Reuses ``PositionLedger.apply_fill``
        fill-by-fill so the sign/avg-price convention matches the real ``positions``
        row exactly, just scoped to one algo.
        """
        async with self._sf() as session:
            result = await session.execute(
                select(Order, Signal)
                .join(Signal, Order.signal_id == Signal.id)
                .where(
                    Order.status == OrderStatus.FILLED.value,
                    Signal.symbol == symbol,
                    Signal.instrument_type == instrument_type,
                    Signal.algo_name == algo_name,
                )
                .order_by(Order.created_at.asc())
            )
            state: PositionState | None = None
            for order, signal in result.all():
                side = Side.BUY if signal.side == Side.BUY.value else Side.SELL
                state = PositionLedger.apply_fill(
                    current=state,
                    fill_qty=order.qty,
                    fill_price=order.avg_price,
                    side=side,
                )
            return state

    async def update_position(
        self, fill: FillEvent, side: Side, symbol: str, instrument_type: str, algo_name: str
    ) -> None:
        async with self._sf() as session:
            async with session.begin():
                await self.update_position_in_session(
                    session, fill, side, symbol, instrument_type, algo_name
                )

    async def update_position_in_session(
        self,
        session: AsyncSession,
        fill: FillEvent,
        side: Side,
        symbol: str,
        instrument_type: str,
        algo_name: str,
    ) -> None:
        """Same as update_position, but runs inside a caller-supplied,
        already-open transaction instead of opening its own."""
        result = await session.execute(
            select(Position)
            .where(
                Position.symbol == symbol,
                Position.instrument_type == instrument_type,
                Position.algo_name == algo_name,
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
                    algo_name=algo_name,
                    net_qty=new_state.net_qty,
                    avg_price=new_state.avg_price,
                    updated_at=datetime.now(UTC),
                )
            )
        else:
            position.net_qty = new_state.net_qty
            position.avg_price = new_state.avg_price
            position.updated_at = datetime.now(UTC)
