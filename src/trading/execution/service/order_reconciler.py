from __future__ import annotations

import logging
from uuid import UUID

from trading.broker.service.zerodha.kite_client import KiteClient
from trading.broker.service.zerodha.models import ZerodhaOrder
from trading.core.schemas import OrderStatus
from trading.execution.service.executor import OrderExecutor
from trading.execution.storage.store import TradingStore
from trading.strategy.storage.models import Signal

logger = logging.getLogger(__name__)

_TERMINAL_REJECT_STATUSES = {"REJECTED", "CANCELLED"}


class OrderReconciler:
    """
    Periodic poll that closes the timeout-divergence gap in
    OrderExecutor._place_with_broker (trading-platform#31): a `place_order`
    call that times out leaves our row REJECTED (or, if the process died
    first, stuck PENDING) even though the order may actually be live at
    Zerodha. Matches such rows back to Zerodha's own order book by
    `client_tag` and corrects them.

    Only meaningful for the real broker -- paper-mode orders never reach
    Zerodha's order book (PaperBroker ignores client_tag), so this should
    only be scheduled when running live.
    """

    def __init__(
        self,
        trading: TradingStore,
        kite_client: KiteClient,
        executor: OrderExecutor,
    ) -> None:
        self._trading = trading
        self._kite_client = kite_client
        self._executor = executor

    async def reconcile_once(self) -> None:
        unresolved = await self._trading.get_unresolved_tagged_orders()
        if not unresolved:
            return

        from anyio import to_thread

        kite_orders = await to_thread.run_sync(self._kite_client.orders)
        by_tag: dict[str, ZerodhaOrder] = {
            tag: kite_order
            for kite_order in kite_orders
            if (tag := kite_order.get("tag"))
        }

        for order, signal in unresolved:
            assert order.client_tag is not None  # filtered by the query itself
            kite_order = by_tag.get(order.client_tag)
            if kite_order is None:
                # Not (yet, or ever) in the broker's order book -- leave for next poll.
                continue
            await self._reconcile_one(order.id, kite_order, signal)

    async def _reconcile_one(
        self, order_id: UUID, kite_order: ZerodhaOrder, signal: Signal
    ) -> None:
        real_kite_order_id = kite_order.get("order_id", "")
        await self._trading.reconcile_order_id(order_id, real_kite_order_id)

        status = kite_order.get("status", "")
        if status == "COMPLETE":
            await self._executor.handle_fill(
                kite_order_id=real_kite_order_id,
                avg_price=float(kite_order.get("average_price", 0.0)),
                filled_qty=int(kite_order.get("filled_quantity", 0)),
                symbol=signal.symbol,
                instrument_type=signal.instrument_type,
                side=signal.side,
            )
            logger.info(
                "OrderReconciler: matched order %s (tag=%s) → COMPLETE, filled",
                real_kite_order_id, kite_order.get("tag"),
            )
        elif status in _TERMINAL_REJECT_STATUSES:
            new_status = (
                OrderStatus.REJECTED if status == "REJECTED" else OrderStatus.CANCELLED
            )
            applied = await self._trading.mark_order_terminal(order_id, new_status)
            if applied:
                logger.info(
                    "OrderReconciler: matched order %s (tag=%s) → %s",
                    real_kite_order_id, kite_order.get("tag"), status,
                )
            else:
                logger.info(
                    "OrderReconciler: order %s (tag=%s) already FILLED -- ignoring "
                    "stale broker %s, not clobbering",
                    real_kite_order_id, kite_order.get("tag"), status,
                )
        else:
            # Still open/pending at the broker -- kite_order_id is now corrected,
            # but no terminal outcome yet; leave status alone for the next poll
            # (or the eventual fill/postback webhook, which now matches by the
            # corrected real kite_order_id).
            logger.info(
                "OrderReconciler: matched order %s (tag=%s) → still %s, kite_order_id corrected",
                real_kite_order_id, kite_order.get("tag"), status,
            )
