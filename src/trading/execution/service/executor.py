from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractRegistry
from trading.core.models import Order
from trading.core.schemas import OrderStatus
from trading.execution.api.interfaces import AbstractTradingStore, Broker
from trading.execution.service.fill_handler import FillHandler
from trading.execution.service.idempotency import is_duplicate
from trading.risk.api.schemas import ValidatedOrderEvent

logger = logging.getLogger(__name__)


class ExecConfig(BaseModel):
    exec_id: str = "direct"


class OrderExecutor(AbstractRegistry):
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

    async def handle(self, event: ValidatedOrderEvent) -> None:  # type: ignore[override]
        order_id = uuid4()
        # Echoed back by the broker on every order/postback for this placement
        # (Kite Connect's `tag`, <=20 chars) -- lets OrderReconciler find this
        # row again by tag even if we never captured the broker's real order id
        # (e.g. the placement call below times out). Persisted up front, before
        # the broker call, so it's on the row regardless of how placement goes.
        client_tag = order_id.hex[:20]
        order = Order(
            id=order_id,
            # kite_order_id is UNIQUE — a shared "" placeholder collides
            # (UniqueViolationError) the moment two orders are both between
            # this insert and their later _persist_order_status() call, which
            # happens routinely once more than one algo/worker can place
            # orders concurrently. order_id is itself unique, so deriving the
            # placeholder from it can never collide.
            kite_order_id=f"PENDING_{order_id}",
            signal_id=event.signal_id,
            status=OrderStatus.PENDING.value,
            qty=event.quantity,
            avg_price=Decimal("0"),
            created_at=self._clock.now(),
            client_tag=client_tag,
            # Denormalized off the signal at creation time (trading-platform#83)
            # so FillHandler can recover the owning algo without a cross-module
            # join. None for test-only/manually-built events -- every real
            # production signal has algo_name set.
            algo_name=event.algo_name,
        )

        if not await self._insert_pending_order(order, event.signal_id):
            return

        kite_order_id, final_status = await self._place_with_broker(event, order_id, client_tag)

        applied = await self._persist_order_status(order_id, kite_order_id, final_status)
        if applied:
            logger.info("OrderExecutor: order %s status=%s", kite_order_id, final_status.value)
        # else: _persist_order_status already logged the actual (reconciler-resolved)
        # outcome -- this placement's own kite_order_id/final_status lost the race and
        # logging them here would contradict what's actually in the DB.

    async def _insert_pending_order(self, order: Order, signal_id: UUID) -> bool:
        """Insert the PENDING order row unless `signal_id` is a duplicate. Returns False (and logs) if it was dropped."""
        async with self._session_factory() as session:
            async with session.begin():
                if await is_duplicate(signal_id, session):
                    logger.info("OrderExecutor: duplicate signal_id %s — dropping", signal_id)
                    return False
                session.add(order)
        return True

    async def _place_with_broker(
        self, event: ValidatedOrderEvent, order_id: UUID, client_tag: str
    ) -> tuple[str, OrderStatus]:
        """
        Place the order with the broker, translating any failure into a REJECTED status.

        "timed out" errors mean the request may have reached the broker before
        the timeout fired — the order could be live even though we mark it
        REJECTED here. Anything else means the request never reached the broker
        (or was explicitly rejected). Flagged distinctly since only the first
        case risks a REJECTED-in-our-DB order that is actually live at the broker.

        On a timeout specifically, `client_tag` (already persisted on the Order
        row before this call) is how OrderReconciler's periodic poll can later
        find this order in the broker's own order book and correct the REJECTED
        status here if it turns out to actually be live -- see trading-platform#31.
        """
        try:
            kite_order_id = await self._broker.place_order(
                symbol=event.symbol,
                side=event.side,
                qty=event.quantity,
                order_type=event.order_type,
                limit_price=event.limit_price,
                instrument_type=event.instrument_type.value,
                tick_log_id=event.tick_log_id,
                client_tag=client_tag,
            )
            return kite_order_id, OrderStatus.PLACED
        except Exception as exc:
            if "timed out" in str(exc).lower():
                logger.critical(
                    "OrderExecutor: broker.place_order TIMED OUT for signal_id=%s — "
                    "order status at broker is UNKNOWN, marking REJECTED locally but it may be live — %s",
                    event.signal_id, exc,
                )
            else:
                logger.error("OrderExecutor: broker.place_order failed — %s", exc)
            return f"FAILED_{order_id}", OrderStatus.REJECTED

    async def _persist_order_status(self, order_id: UUID, kite_order_id: str, status: OrderStatus) -> bool:
        """
        Writes the placement result, unless OrderReconciler already resolved
        this order first (trading-platform#31 review): the `Broker.place_order`
        call above can, under a still-open anyio bug (#85), keep running in
        the background past its own timeout and return successfully well
        after a poll already matched this order by `client_tag` and applied a
        fill or a terminal status. Only PENDING -> anything is safe to write
        here; once the row has moved off PENDING, whichever resolution got
        there first (a real fill or a reconciler correction) wins and this
        late write becomes a no-op rather than clobbering it.

        Returns False on that no-op path -- callers must check this before
        logging `status`/`kite_order_id`, since those are this placement's
        own (possibly stale) values, not what's actually persisted.
        """

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        async def _attempt() -> bool:
            async with self._session_factory() as session:
                async with session.begin():
                    row = await session.get(Order, order_id, with_for_update=True)
                    if row is None:
                        return False
                    if row.status != OrderStatus.PENDING.value:
                        logger.info(
                            "OrderExecutor: order %s already resolved to status=%s "
                            "(likely by OrderReconciler) — not overwriting with late "
                            "placement result status=%s",
                            order_id, row.status, status.value,
                        )
                        return False
                    row.kite_order_id = kite_order_id
                    row.status = status.value
                    return True

        try:
            return await _attempt()
        except Exception as exc:
            logger.critical(
                "UNRECOVERABLE: order placed (kite_order_id=%s) but DB update failed after 3 attempts — error=%s",
                kite_order_id, exc,
            )
            return False

    async def handle_fill(
        self,
        kite_order_id: str,
        avg_price: float,
        filled_qty: int,
        symbol: str,
        instrument_type: str,
        side: str,
        tick_log_id: int = 0,
    ) -> bool:
        """Returns True if this call actually applied the fill -- see FillHandler.handle."""
        return await self._fill_handler.handle(
            kite_order_id, avg_price, filled_qty, symbol, instrument_type, side, tick_log_id
        )
