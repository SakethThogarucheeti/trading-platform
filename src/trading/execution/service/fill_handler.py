from __future__ import annotations

import logging

from trading.core.clock import Clock, SystemClock
from trading.core.schemas import OrderStatus, Side
from trading.execution.api.interfaces import AbstractTradingStore
from trading.execution.api.schemas import FillEvent
from trading.execution.service.position_accountant import PositionAccountant
from trading.execution.storage.store import NotFoundError

logger = logging.getLogger(__name__)

# Non-null sentinel for the positions table's algo_name PK column
# (trading-platform#83) when the Order's own denormalized algo_name is None
# -- e.g. a test-only/manually-built ValidatedOrderEvent. Every real
# production signal has algo_name set (di/providers/algo_pipeline.py always
# sets it), so this path is not expected to be hit in live trading.
UNKNOWN_ALGO_NAME = "UNKNOWN"


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
        fill_side = Side(side)
        try:
            # The order-status transition and the position/PnL-aggregate
            # writes it triggers must commit or roll back together -- three
            # independently-committed transactions here could leave the
            # order marked FILLED with the position/PnL update lost, or vice
            # versa, on a crash between them (trading-platform#91).
            async with self._trading.transaction() as session:
                applied = await self._trading.update_order_status_in_session(
                    session, kite_order_id, OrderStatus.FILLED, avg_price
                )
                if not applied:
                    logger.info(
                        "FillHandler: order %s already FILLED — skipping duplicate fill "
                        "application",
                        kite_order_id,
                    )
                    return False
                algo_name = (
                    await self._trading.get_order_algo_name_in_session(session, kite_order_id)
                    or UNKNOWN_ALGO_NAME
                )
                await self._accountant.apply_fill(
                    session, fill, fill_side, symbol, instrument_type, algo_name
                )
        except NotFoundError as exc:
            logger.warning("FillHandler: fill for unknown order %s — %s", kite_order_id, exc)
            return False
        logger.info("FillHandler: fill %s avg=%.2f qty=%d", kite_order_id, avg_price, filled_qty)
        return True
