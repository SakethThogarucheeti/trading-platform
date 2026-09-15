"""
Centralized query service for filled trades and derived P&L.

The dashboard API's /api/pnl and /api/pnl/by-algo, the ops-only /api/trades
endpoint (see routers/data.py's get_trades -- implemented and tested, but
not called by the dashboard frontend; intended for direct/curl use by
whoever operates the bot), and the report engine (fetch_report_data) all
use this module so the Order+Signal join and cost model exist in exactly
one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.clock import Clock
from trading.core.fifo import match_against
from trading.core.models import Order, Signal
from trading.core.schemas import OrderStatus
from trading.reports.pnl import DEFAULT_COSTS, TradeCosts


@dataclass
class FilledTrade:
    """One filled order with its associated signal context."""

    order_id: str
    kite_order_id: str
    signal_id: str
    algo_name: str
    strategy_id: str
    symbol: str
    instrument_type: str
    side: str
    signal_type: str
    qty: int
    avg_price: float
    gross: float        # signed: positive = profit contribution
    cost: float         # always positive
    net: float          # gross - cost
    filled_at: datetime


@dataclass
class PnlSummary:
    """Aggregate P&L across a set of trades."""

    gross: float
    costs: float
    net: float


def _local_day_start_utc(clock: Clock, when: datetime) -> datetime:
    """Start of the local trading-calendar day containing `when`, as UTC."""
    if when == datetime.min.replace(tzinfo=UTC):
        return when  # SimulatedClock before first advance() -- avoid tz-conversion overflow
    local = when.astimezone(clock.tz)
    return datetime(local.year, local.month, local.day, tzinfo=clock.tz).astimezone(UTC)


async def fetch_filled_trades(
    session_factory: async_sessionmaker[AsyncSession],
    start: datetime,
    end: datetime,
    clock: Clock,
    algo_name: str = "",
    costs: TradeCosts = DEFAULT_COSTS,
) -> list[FilledTrade]:
    """
    Return all FILLED orders in [start, end] with full signal context and P&L per leg.

    ``gross`` is each order's FIFO-matched realized P&L (trading-platform#89),
    not signed cash flow: an opening fill (one that extends a position)
    contributes 0 to ``gross``; a closing fill carries the profit/loss
    matched against the opposing fills it closed out. Matching is per
    symbol, mirroring the granularity `PositionAccountant` actually tracks
    positions at (one net position per symbol, not per algo) -- if two
    algos trade the same symbol, which one's fill gets "credited" with
    closing the position is inherent to FIFO-by-symbol accounting and
    matches live reality, not an artifact of this reporting fix.

    FIFO state is seeded from fills since the start of the local trading day
    containing `start`, not from `start` itself, so a closing fill just
    inside the window still matches correctly against an opening fill from
    earlier the same day -- seeded fills before `start` are used only to
    prime the queues and are not included in the returned list. This bound
    is safe (not approximate) because this is an intraday-only bot with a
    daily EOD square-off (#96): positions are flat at every local-day
    boundary, so no fill on one trading day can ever need to match against
    a fill from an earlier day.

    Optional ``algo_name`` filter narrows the *returned* results to one algo,
    but does not narrow FIFO seeding -- a closing fill from a filtered-out
    algo must still drain the shared per-symbol queue correctly.
    """
    fetch_start = _local_day_start_utc(clock, start)
    async with session_factory() as session:
        result = await session.execute(
            select(Order, Signal)
            .join(Signal, Order.signal_id == Signal.id)
            .where(
                Order.status == OrderStatus.FILLED.value,
                Order.created_at >= fetch_start,
                Order.created_at <= end,
            )
            .order_by(Order.created_at)
        )
        rows = result.all()

    queues: dict[str, tuple[list[tuple[int, Decimal]], list[tuple[int, Decimal]]]] = {}
    trades: list[FilledTrade] = []
    for order, signal in rows:
        price = float(order.avg_price)
        price_dec: Decimal = order.avg_price
        long_queue, short_queue = queues.setdefault(signal.symbol, ([], []))
        if signal.side == "BUY":
            matched, remaining = match_against(short_queue, order.qty, price_dec, sign=-1)
            if remaining > 0:
                long_queue.append((remaining, price_dec))
        else:
            matched, remaining = match_against(long_queue, order.qty, price_dec, sign=1)
            if remaining > 0:
                short_queue.append((remaining, price_dec))

        created_at = order.created_at
        if created_at.tzinfo is None:
            # sqlite (used by tests) doesn't round-trip tzinfo through
            # DateTime(timezone=True) the way Postgres does -- values are
            # always stored/read as UTC, so a naive read means UTC.
            created_at = created_at.replace(tzinfo=UTC)
        if created_at < start:
            continue  # seeding-only fill: primes FIFO state, not part of the requested window
        if algo_name and signal.algo_name != algo_name:
            continue

        gross = float(matched)
        cost = costs.cost_for_fill(signal.side, order.qty, price)
        trades.append(
            FilledTrade(
                order_id=str(order.id),
                kite_order_id=order.kite_order_id,
                signal_id=str(signal.id),
                algo_name=signal.algo_name or "default",
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                instrument_type=signal.instrument_type,
                side=signal.side,
                signal_type=signal.signal_type,
                qty=order.qty,
                avg_price=price,
                gross=gross,
                cost=cost,
                net=gross - cost,
                filled_at=order.created_at,
            )
        )
    return trades


def summarize(trades: list[FilledTrade]) -> PnlSummary:
    """Aggregate a trade list into gross/costs/net totals."""
    gross = sum(t.gross for t in trades)
    costs = sum(t.cost for t in trades)
    return PnlSummary(gross=gross, costs=costs, net=gross - costs)


def summarize_by_algo(trades: list[FilledTrade]) -> dict[str, PnlSummary]:
    """Group trades by algo_name and return per-algo P&L summaries."""
    grouped: dict[str, list[FilledTrade]] = {}
    for t in trades:
        grouped.setdefault(t.algo_name, []).append(t)
    return {name: summarize(group) for name, group in grouped.items()}
