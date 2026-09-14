from __future__ import annotations

import logging

from trading.core.clock import Clock, SystemClock
from trading.core.schemas import OrderStatus, Side
from trading.execution.api.interfaces import AbstractTradingStore
from trading.execution.api.schemas import FillEvent
from trading.execution.service.position_accountant import PositionAccountant
from trading.execution.storage.store import NotFoundError

logger = logging.getLogger(__name__)


class FillHandler:
    """Processes fill notifications: marks order FILLED, applies fill to position."""

    def __init__(
        self,
        trading: AbstractTradingStore,
        accountant: PositionAccountant,
        clock: Clock | None = None,
    ) -> None:
        self._trading = trading
        self._accountant = accountant
        self._clock: Clock = clock or SystemClock()

    async def handle(
        self,
        kite_order_id: str,
        avg_price: float,
        filled_qty: int,
        symbol: str,
        instrument_type: str,
        side: str,
        tick_log_id: int = 0,
    ) -> bool:
        """
        Returns True if this call actually applied the fill, False if it was
        a no-op (unknown order, or already FILLED -- see
        TradingStore.update_order_status). Callers must check this before
        logging the fill as applied, since a webhook and OrderReconciler can
        both call this for the same kite_order_id (trading-platform#31
        review) and only one of them actually moves the position.
        """
        fill = FillEvent(
            kite_order_id=kite_order_id,
            avg_price=avg_price,
            filled_qty=filled_qty,
            timestamp=self._clock.now(),
            tick_log_id=tick_log_id,
        )
        try:
            applied = await self._trading.update_order_status(
                kite_order_id, OrderStatus.FILLED, avg_price
            )
        except NotFoundError as exc:
            logger.warning("FillHandler: fill for unknown order %s — %s", kite_order_id, exc)
            return False
        if not applied:
            logger.info(
                "FillHandler: order %s already FILLED — skipping duplicate fill application",
                kite_order_id,
            )
            return False
        fill_side = Side(side)
        await self._accountant.apply_fill(fill, fill_side, symbol, instrument_type)
        logger.info("FillHandler: fill %s avg=%.2f qty=%d", kite_order_id, avg_price, filled_qty)
        return True
