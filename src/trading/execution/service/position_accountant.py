from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from trading.core.clock import Clock, SystemClock
from trading.core.fifo import match_against
from trading.core.schemas import Side
from trading.execution.api.interfaces import (
    AbstractPositionStore,
    AbstractTradingStore,
    CacherFactory,
)
from trading.execution.api.schemas import FillEvent

logger = logging.getLogger(__name__)

_FifoQueues = tuple[list[tuple[int, Decimal]], list[tuple[int, Decimal]]]


class PositionAccountant:
    """Single entry point for all position state updates after a fill."""

    def __init__(
        self,
        position: AbstractPositionStore,
        trading: AbstractTradingStore,
        factory: CacherFactory,
        clock: Clock | None = None,
    ) -> None:
        self._position = position
        self._trading = trading
        self._factory = factory
        self._clock: Clock = clock or SystemClock()
        # Per-symbol FIFO long/short queues, hydrated lazily from today's
        # already-persisted fills (see _get_queues) — not a new persistence
        # mechanism, just an in-memory replay cache keyed by the trading day
        # so it self-corrects across a process restart or a day rollover.
        self._queues: dict[str, tuple[date, _FifoQueues]] = {}

    async def apply_fill(
        self,
        session: AsyncSession,
        fill: FillEvent,
        side: Side,
        symbol: str,
        instrument_type: str,
        algo_name: str,
    ) -> None:
        """
        `session` must be an already-open transaction (from
        `AbstractTradingStore.transaction()`) that the caller commits/rolls
        back once this returns -- the position update and the PnL-aggregate
        increment below must land atomically together with whatever else the
        caller is writing in the same transaction (trading-platform#91/#96),
        rather than as two independently-committed writes.

        `algo_name` only scopes the per-algo `positions` row
        (trading-platform#83) -- the FIFO PnL matching below stays
        symbol-scoped, out of #83's scope.
        """
        await self._position.update_position_in_session(
            session, fill, side, symbol, instrument_type, algo_name
        )
        today = self._clock.today()
        long_queue, short_queue = await self._get_queues(
            session, symbol, today, fill.kite_order_id
        )

        # `FillEvent.avg_price` is a `float` (trading-types#... pinned schema,
        # out of scope here) -- convert once at this boundary via `str()` (no
        # binary-float artifact) so every FIFO-matching arithmetic op below is
        # exact Decimal, not float (trading-platform#92).
        price = Decimal(str(fill.avg_price))

        if side == Side.BUY:
            realized, remaining = match_against(short_queue, fill.filled_qty, price, sign=-1)
            if remaining > 0:
                long_queue.append((remaining, price))
        else:
            realized, remaining = match_against(long_queue, fill.filled_qty, price, sign=1)
            if remaining > 0:
                short_queue.append((remaining, price))
        self._queues[symbol] = (today, (long_queue, short_queue))

        await self._trading.increment_pnl_aggregate_in_session(session, today, realized)
        await self._factory.api().invalidate_pnl(today)  # type: ignore[attr-defined]

    async def _get_queues(
        self, session: AsyncSession, symbol: str, today: date, current_kite_order_id: str
    ) -> _FifoQueues:
        """`session` is the same in-flight transaction apply_fill was given --
        hydration must stay on that connection rather than opening a second,
        interleaved session mid-transaction (trading-platform#91)."""
        cached = self._queues.get(symbol)
        if cached is not None and cached[0] == today:
            return cached[1]

        long_queue: list[tuple[int, Decimal]] = []
        short_queue: list[tuple[int, Decimal]] = []
        fills = await self._trading.get_filled_fills_in_session(
            session, today, symbol, exclude_kite_order_id=current_kite_order_id
        )
        for fill_side, qty, price in fills:
            if fill_side == Side.BUY.value:
                _, remaining = match_against(short_queue, qty, price, sign=-1)
                if remaining > 0:
                    long_queue.append((remaining, price))
            else:
                _, remaining = match_against(long_queue, qty, price, sign=1)
                if remaining > 0:
                    short_queue.append((remaining, price))

        queues = (long_queue, short_queue)
        self._queues[symbol] = (today, queues)
        return queues
