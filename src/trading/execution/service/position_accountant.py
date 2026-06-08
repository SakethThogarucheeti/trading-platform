from __future__ import annotations

import logging

from trading.core.clock import Clock, SystemClock
from trading.core.schemas import Side
from trading.execution.api.interfaces import AbstractPositionStore, CacherFactory
from trading.execution.api.schemas import FillEvent

logger = logging.getLogger(__name__)


class PositionAccountant:
    """Single entry point for all position state updates after a fill."""

    def __init__(
        self,
        position: AbstractPositionStore,
        factory: CacherFactory,
        clock: Clock | None = None,
    ) -> None:
        self._position = position
        self._factory = factory
        self._clock: Clock = clock or SystemClock()

    async def apply_fill(
        self,
        fill: FillEvent,
        side: Side,
        symbol: str,
        instrument_type: str,
    ) -> None:
        await self._position.update_position(fill, side, symbol, instrument_type)
        today = self._clock.now().date()
        self._factory.pnl().increment_sync(today, side, fill.avg_price, fill.filled_qty)  # type: ignore[attr-defined]
        await self._factory.api().invalidate_pnl(today)  # type: ignore[attr-defined]
