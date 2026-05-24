from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from trading.broker.base.broker import Broker
from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractRegistry
from trading.core.models import Order
from trading.core.schemas import OrderStatus, ValidatedOrderEvent
from trading.execution.fill_handler import FillHandler
from trading.execution.idempotency import is_duplicate
from trading.storage.stores.trading import AbstractTradingStore

logger = logging.getLogger(__name__)


class ExecConfig(BaseModel):
    """Configuration for the execution stage."""

    exec_id: str = "direct"  # "paper" | "direct"


class OrderExecutor(AbstractRegistry):
    """
    Routes a ValidatedOrderEvent to the broker and handles fills.

    Places the order via the broker. Fills arrive via handle_fill() — called
    either by a Zerodha postback webhook (live) or by PaperBroker.place_order()
    (paper trading simulation).

    handle() always returns None (fire-and-forget terminal stage).
    """

    def __init__(
        self,
        config: ExecConfig,
        broker: Broker,
        session_factory: async_sessionmaker[AsyncSession],
        trading: AbstractTradingStore,
        fill_handler: FillHandler,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._broker = broker
        self._session_factory = session_factory
        self._trading = trading
        self._fill_handler = fill_handler
        self._clock: Clock = clock or SystemClock()

    @property
    def config(self) -> ExecConfig:
        return self._config

    # ------------------------------------------------------------------
    # AbstractRegistry
    # ------------------------------------------------------------------

    async def handle(self, event: ValidatedOrderEvent) -> None:
        order_id = uuid4()
        order = Order(
            id=order_id,
            kite_order_id="",
            signal_id=event.signal_id,
            status=OrderStatus.PENDING.value,
            qty=event.quantity,
            avg_price=Decimal("0"),
            created_at=self._clock.now(),
        )

        # Idempotency check + order insert must be atomic (prevent TOCTOU)
        async with self._session_factory() as session:
            async with session.begin():
                if await is_duplicate(event.signal_id, session):
                    logger.info("OrderExecutor: duplicate signal_id %s — dropping", event.signal_id)
                    return
                session.add(order)

        # Broker call outside transaction. PaperBroker fires handle_fill() inline;
        # live broker waits for the Zerodha postback to call handle_fill().
        try:
            kite_order_id = await self._broker.place_order(
                symbol=event.symbol,
                side=event.side,
                qty=event.quantity,
                order_type=event.order_type,
                limit_price=event.limit_price,
                instrument_type=event.instrument_type.value,
                tick_log_id=event.tick_log_id,
            )
            final_status = OrderStatus.PLACED
        except Exception as exc:
            logger.error("OrderExecutor: broker.place_order failed — %s", exc)
            kite_order_id = f"FAILED_{order_id}"
            final_status = OrderStatus.REJECTED

        await self._persist_order_status(order_id, kite_order_id, final_status)
        logger.info("OrderExecutor: order %s status=%s", kite_order_id, final_status.value)

    async def _persist_order_status(
        self, order_id: UUID, kite_order_id: str, status: OrderStatus
    ) -> None:
        """Write kite_order_id + status back to the Order row, with retries."""

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        async def _attempt() -> None:
            async with self._session_factory() as session:
                async with session.begin():
                    row = await session.get(Order, order_id)
                    if row is not None:
                        row.kite_order_id = kite_order_id
                        row.status = status.value

        try:
            await _attempt()
        except Exception as exc:
            logger.critical(
                "UNRECOVERABLE: order placed on broker (kite_order_id=%s) but DB update "
                "failed after 3 attempts — manual reconciliation needed. error=%s",
                kite_order_id,
                exc,
            )

    async def handle_fill(
        self,
        kite_order_id: str,
        avg_price: float,
        filled_qty: int,
        symbol: str,
        instrument_type: str,
        side: str,
        tick_log_id: int = 0,
    ) -> None:
        """
        Process a fill notification.

        Called by the Zerodha postback webhook (live) or by PaperBroker (paper trading).
        """
        await self._fill_handler.handle(
            kite_order_id, avg_price, filled_qty, symbol, instrument_type, side, tick_log_id
        )
