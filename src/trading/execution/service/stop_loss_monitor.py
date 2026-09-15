"""Periodic price-based stop-loss exit monitor (trading-platform#65).

Implements Option 3 from the posted design writeup: land Option 2's periodic
poll now as an immediate, reviewable backstop, with the properly-scoped
per-tick version (Option 1) deferred as a follow-up once trading-platform#8
(per-algo position scoping) lands and gives a real position-level stop
mechanism to build against. This checks every open position against its
most recent entry signal's stop_distance every N seconds -- not on every
tick -- so a fast intraday move between polls can still blow through the
stop before the next check fires. That is a real, documented gap versus a
true per-tick monitor, not something to treat as equivalent.

Unlike eod_square_off (which fabricates a FILLED Order directly via
PositionAccountant.apply_fill -- explicitly a paper-trading-only shortcut,
see that module's docstring), a breach here is routed through the real
OrderExecutor.handle path, which calls Broker.place_order -- so it actually
reaches the broker in live mode, not just paper.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading.broker.service.paper_broker import AbstractPriceStore
from trading.core.clock import Clock
from trading.core.schemas import (
    InstrumentType,
    OrderStatus,
    OrderType,
    Side,
    SignalType,
    ValidatedOrderEvent,
)
from trading.execution.service.executor import OrderExecutor
from trading.execution.storage.models import Order, Position
from trading.execution.storage.store import TradingStore
from trading.strategy.storage.models import Signal

logger = logging.getLogger(__name__)

# Sentinel algo_name/strategy_id for this monitor's synthetic exit signals --
# same rationale as eod_square_off.EOD_SQUARE_OFF_NAME: fetch_filled_trades()
# buckets a null algo_name into "default", which would misattribute this
# netted, not-algo-specific close to whichever algo happens to be named
# "default" in /api/pnl/by-algo.
STOP_LOSS_MONITOR_NAME = "stop_loss_monitor"


async def _latest_entry_stop_distance(
    session: AsyncSession, symbol: str, instrument_type: str
) -> Decimal | None:
    """Most recent ENTRY signal's stop_distance for this (symbol, instrument_type).

    A position that averaged in across 2+ entries with different
    stop_distance values has no single well-defined stop level -- using the
    most recent entry is the documented first-cut simplification from the
    design writeup's sub-problem 1 (correct for a single-fill position,
    approximate once averaged-in).
    """
    result = await session.execute(
        select(Signal.stop_distance)
        .where(
            Signal.symbol == symbol,
            Signal.instrument_type == instrument_type,
            Signal.signal_type == SignalType.ENTRY.value,
        )
        .order_by(Signal.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _has_pending_exit(session: AsyncSession, symbol: str, instrument_type: str) -> bool:
    """True if this monitor already has an in-flight (not yet resolved) exit
    for this position -- guards against re-firing a second exit order every
    poll while the first one is still PENDING with the broker."""
    result = await session.execute(
        select(Order.id)
        .join(Signal, Order.signal_id == Signal.id)
        .where(
            Signal.symbol == symbol,
            Signal.instrument_type == instrument_type,
            Signal.strategy_id == STOP_LOSS_MONITOR_NAME,
            Order.status == OrderStatus.PENDING.value,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _is_breached(
    *, side_is_long: bool, entry_price: Decimal, stop_distance: Decimal, last_price: Decimal
) -> bool:
    if stop_distance <= 0:
        return False  # no stop configured for this entry (e.g. a synthetic 0.0 sentinel)
    if side_is_long:
        return last_price <= entry_price - stop_distance
    return last_price >= entry_price + stop_distance


async def check_stop_losses(
    trading: TradingStore,
    order_executor: OrderExecutor,
    price_store: AbstractPriceStore,
    clock: Clock,
) -> None:
    """Scan all open positions once and route a synthetic EXIT for any breach."""
    async with trading.transaction() as session:
        result = await session.execute(select(Position).where(Position.net_qty != 0))
        candidates = [(pos.symbol, pos.instrument_type) for pos in result.scalars().all()]

    for symbol, instrument_type in candidates:
        async with trading.transaction() as session:
            result = await session.execute(
                select(Position)
                .where(
                    Position.symbol == symbol,
                    Position.instrument_type == instrument_type,
                )
                .with_for_update()
            )
            pos = result.scalar_one_or_none()
            if pos is None or pos.net_qty == 0:
                continue  # already flattened since the enumeration above

            stop_distance = await _latest_entry_stop_distance(session, symbol, instrument_type)
            if not stop_distance:
                continue
            if await _has_pending_exit(session, symbol, instrument_type):
                continue

            side_is_long = pos.net_qty > 0
            entry_price = pos.avg_price
            net_qty = pos.net_qty

        raw_price = price_store.get(symbol)
        if raw_price is None:
            continue
        last_price = Decimal(str(raw_price))
        if not _is_breached(
            side_is_long=side_is_long,
            entry_price=entry_price,
            stop_distance=stop_distance,
            last_price=last_price,
        ):
            continue

        side = Side.SELL if side_is_long else Side.BUY
        qty = abs(net_qty)
        event = ValidatedOrderEvent(
            signal_id=uuid4(),
            symbol=symbol,
            instrument_type=InstrumentType(instrument_type),
            side=side,
            quantity=qty,
            order_type=OrderType.MARKET,
            tick_log_id=0,
            timestamp=clock.now(),
            strategy_id=STOP_LOSS_MONITOR_NAME,
            algo_name=STOP_LOSS_MONITOR_NAME,
            signal_type=SignalType.EXIT,
            stop_distance=0.0,
        )
        await trading.save_signal(event)
        await order_executor.handle(event)
        logger.warning(
            "Stop-loss breach: %s %s x%d @ %.4f (entry %.4f, stop_distance %.4f)",
            side.value,
            symbol,
            qty,
            float(last_price),
            float(entry_price),
            float(stop_distance),
        )
