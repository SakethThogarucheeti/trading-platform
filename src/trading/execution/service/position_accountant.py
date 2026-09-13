from __future__ import annotations

import logging
from datetime import date

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

_FifoQueues = tuple[list[tuple[int, float]], list[tuple[int, float]]]


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
        fill: FillEvent,
        side: Side,
        symbol: str,
        instrument_type: str,
    ) -> None:
        await self._position.update_position(fill, side, symbol, instrument_type)
        today = self._clock.now().date()
        long_queue, short_queue = await self._get_queues(symbol, today, fill.kite_order_id)

        if side == Side.BUY:
            realized, remaining = match_against(
                short_queue, fill.filled_qty, fill.avg_price, sign=-1
            )
            if remaining > 0:
                long_queue.append((remaining, fill.avg_price))
        else:
            realized, remaining = match_against(long_queue, fill.filled_qty, fill.avg_price, sign=1)
            if remaining > 0:
                short_queue.append((remaining, fill.avg_price))
        self._queues[symbol] = (today, (long_queue, short_queue))

        await self._trading.increment_pnl_aggregate(today, realized)  # type: ignore[attr-defined]
        await self._factory.api().invalidate_pnl(today)  # type: ignore[attr-defined]

    async def _get_queues(
        self, symbol: str, today: date, current_kite_order_id: str
    ) -> _FifoQueues:
        cached = self._queues.get(symbol)
        if cached is not None and cached[0] == today:
            return cached[1]

        long_queue: list[tuple[int, float]] = []
        short_queue: list[tuple[int, float]] = []
        fills = await self._trading.get_filled_fills(
            today, symbol, exclude_kite_order_id=current_kite_order_id
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
