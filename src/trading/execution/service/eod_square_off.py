"""End-of-day flat-out for paper trading: closes any open netted position.

Runs on the scheduler's 15:29 IST cron. Goes through the same Signal + Order +
PositionAccountant path a normal fill takes (see FillHandler/OrderExecutor)
so the exit gets a full audit trail and shows up in /api/pnl — a direct
position_store.update_position() call would zero the position with no
Order/Signal row at all, silently dropping the exit fill from every PnL view.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from trading.broker.service.paper_broker import AbstractPriceStore
from trading.core.clock import Clock
from trading.core.schemas import (
    FillEvent,
    InstrumentType,
    OrderStatus,
    OrderType,
    Side,
    SignalType,
    ValidatedOrderEvent,
)
from trading.execution.service.position_accountant import PositionAccountant
from trading.execution.storage.models import Order, Position
from trading.execution.storage.store import TradingStore

logger = logging.getLogger(__name__)

# Sentinel algo_name/strategy_id for the synthetic exit signal/order this
# generates. fetch_filled_trades() buckets a null algo_name into "default",
# which would misattribute this netted, not-algo-specific close to whichever
# algo happens to be named "default" in /api/pnl/by-algo — a distinct
# sentinel keeps it in its own bucket instead.
EOD_SQUARE_OFF_NAME = "eod_square_off"


async def square_off_open_positions(
    trading: TradingStore,
    accountant: PositionAccountant,
    price_store: AbstractPriceStore,
    clock: Clock,
) -> None:
    # Unlocked enumeration only -- just to find candidate (symbol, instrument_type)
    # pairs to check. The actual net_qty used to build the exit must come from a
    # locked re-read taken immediately before use (below), not this snapshot: a
    # real fill landing on the same position between this scan and the exit being
    # applied would otherwise close a stale, wrong quantity (trading-platform#96).
    async with trading.transaction() as session:
        result = await session.execute(select(Position).where(Position.net_qty != 0))
        candidates = [
            (pos.symbol, pos.instrument_type, pos.algo_name) for pos in result.scalars().all()
        ]

    for symbol, instrument_type, algo_name in candidates:
        async with trading.transaction() as session:
            result = await session.execute(
                select(Position)
                .where(
                    Position.symbol == symbol,
                    Position.instrument_type == instrument_type,
                    Position.algo_name == algo_name,
                )
                .with_for_update()
            )
            pos = result.scalar_one_or_none()
            if pos is None or pos.net_qty == 0:
                # Already flattened by a real fill since the enumeration above --
                # nothing to square off.
                continue

            raw_price = price_store.get(pos.symbol)
            last_price = float(raw_price) if raw_price is not None else float(pos.avg_price)
            side = Side.SELL if pos.net_qty > 0 else Side.BUY
            qty = abs(pos.net_qty)
            now = clock.now()
            # algo_name in the sentinel (trading-platform#83) avoids a
            # unique-constraint collision across algos on the same
            # symbol/instrument_type/day, now that they hold independent rows.
            kite_order_id = (
                f"EOD_{pos.symbol}_{pos.instrument_type}_{pos.algo_name}_"
                f"{clock.today().isoformat()}"
            )

            event = ValidatedOrderEvent(
                signal_id=uuid4(),
                symbol=pos.symbol,
                instrument_type=InstrumentType(pos.instrument_type),
                side=side,
                quantity=qty,
                order_type=OrderType.MARKET,
                tick_log_id=0,
                timestamp=now,
                strategy_id=EOD_SQUARE_OFF_NAME,
                # The position's own owning algo, not the EOD_SQUARE_OFF_NAME
                # sentinel (trading-platform#83) -- this exit's economic
                # attribution belongs to whichever algo held the position;
                # strategy_id keeps identifying the signal as scheduler-generated.
                algo_name=pos.algo_name,
                signal_type=SignalType.EXIT,
                stop_distance=0.0,
            )
            await trading.save_signal(event)
            await trading.save_order(
                Order(
                    id=uuid4(),
                    kite_order_id=kite_order_id,
                    signal_id=event.signal_id,
                    status=OrderStatus.FILLED.value,
                    qty=qty,
                    avg_price=Decimal(str(last_price)),
                    created_at=now,
                    algo_name=pos.algo_name,
                )
            )
            fill = FillEvent(
                kite_order_id=kite_order_id,
                avg_price=last_price,
                filled_qty=qty,
                timestamp=now,
            )
            await accountant.apply_fill(
                session, fill, side, pos.symbol, pos.instrument_type, pos.algo_name
            )
            logger.info(
                "EOD square-off: %s %s x%d @ %.2f (algo=%s)",
                side.value, pos.symbol, qty, last_price, pos.algo_name,
            )
