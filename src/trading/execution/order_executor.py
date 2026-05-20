from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.broker.base.broker import Broker
from trading.broker.paper_broker import AbstractPriceStore
from trading.core.messaging import AbstractRegistry
from trading.core.models import Order
from trading.core.schemas import (
    FillEvent,
    OrderStatus,
    Side,
    ValidatedOrderEvent,
)
from trading.execution.idempotency import is_duplicate
from trading.storage.stores.trading import AbstractTradingStore, NotFoundError

logger = logging.getLogger(__name__)


class ExecConfig(BaseModel):
    """Configuration for the execution stage."""

    exec_id: str = "direct"  # "paper" | "direct"


class OrderExecutor(AbstractRegistry):
    """
    Routes a ValidatedOrderEvent to the broker and handles fills.

    exec_id="paper"  — simulates an immediate fill at the last known price
    exec_id="direct" — places a real order via the broker

    handle() always returns None (fire-and-forget terminal stage).
    """

    def __init__(
        self,
        config: ExecConfig,
        broker: Broker,
        session_factory: async_sessionmaker[AsyncSession],
        trading: AbstractTradingStore,
        price_store: AbstractPriceStore | None = None,
    ) -> None:
        self._config = config
        self._broker = broker
        self._session_factory = session_factory
        self._trading = trading
        self._price_store = price_store if config.exec_id == "paper" else None

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
            created_at=datetime.now(UTC),
        )

        # Idempotency check + order insert must be atomic (prevent TOCTOU)
        async with self._session_factory() as session:
            async with session.begin():
                if await is_duplicate(event.signal_id, session):
                    logger.info("OrderExecutor: duplicate signal_id %s — dropping", event.signal_id)
                    return
                session.add(order)

        # Broker call outside transaction
        try:
            kite_order_id = await self._broker.place_order(
                symbol=event.symbol,
                side=event.side,
                qty=event.quantity,
                order_type=event.order_type,
                limit_price=event.limit_price,
            )
            final_status = OrderStatus.PLACED
        except Exception as exc:
            logger.error("OrderExecutor: broker.place_order failed — %s", exc)
            kite_order_id = f"FAILED_{order_id}"
            final_status = OrderStatus.REJECTED

        # Write kite_order_id + final status back to the order row
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(Order, order_id)
                if row is not None:
                    row.kite_order_id = kite_order_id
                    row.status = final_status.value

        logger.info("OrderExecutor: order %s status=%s", kite_order_id, final_status.value)

        # Paper trading: simulate immediate fill
        if self._price_store is not None and final_status == OrderStatus.PLACED:
            _ps = self._price_store
            if hasattr(_ps, "fill_price"):
                _fp = _ps.fill_price(event.symbol, event.side)  # type: ignore[attr-defined]
                fill_price: float | None = float(_fp) if _fp is not None else None  # type: ignore[arg-type]
            else:
                raw = _ps.get(event.symbol)  # type: ignore[attr-defined]
                fill_price = float(raw) if raw is not None else None
            if fill_price is None:
                logger.warning("OrderExecutor: no price known for %s — fill skipped", event.symbol)
            else:
                await self._handle_fill(
                    kite_order_id=kite_order_id,
                    avg_price=fill_price,
                    filled_qty=event.quantity,
                    symbol=event.symbol,
                    instrument_type=event.instrument_type.value,
                    side=event.side.value,
                )

    async def _handle_fill(
        self,
        kite_order_id: str,
        avg_price: float,
        filled_qty: int,
        symbol: str,
        instrument_type: str,
        side: str,
    ) -> None:
        fill = FillEvent(
            kite_order_id=kite_order_id,
            avg_price=avg_price,
            filled_qty=filled_qty,
            timestamp=datetime.now(UTC),
        )
        try:
            await self._trading.update_order_status(kite_order_id, OrderStatus.FILLED, avg_price)
        except NotFoundError:
            logger.warning(
                "OrderExecutor: fill for unknown order %s — skipping", kite_order_id
            )
            return
        await self._trading.update_position(fill, Side(side), symbol, instrument_type)
        logger.info("OrderExecutor: fill %s avg=%.2f qty=%d", kite_order_id, avg_price, filled_qty)
