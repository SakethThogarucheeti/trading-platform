"""
Centralized query service for filled trades and derived P&L.

Both the dashboard API (/api/pnl, /api/pnl/by-algo, /api/trades) and the
report engine (fetch_report_data) use this module so the Order+Signal join
and cost model exist in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


def _signed_gross(side: str, qty: int, avg_price: float) -> float:
    sign = 1.0 if side == "SELL" else -1.0
    return sign * avg_price * qty


async def fetch_filled_trades(
    session_factory: async_sessionmaker[AsyncSession],
    start: datetime,
    end: datetime,
    algo_name: str = "",
    costs: TradeCosts = DEFAULT_COSTS,
) -> list[FilledTrade]:
    """
    Return all FILLED orders in [start, end] with full signal context and P&L per leg.

    Optional ``algo_name`` filter narrows results to one algo.
    """
    async with session_factory() as session:
        conditions = [
            Order.status == OrderStatus.FILLED.value,
            Order.created_at >= start,
            Order.created_at <= end,
        ]
        if algo_name:
            conditions.append(Signal.algo_name == algo_name)
        result = await session.execute(
            select(Order, Signal)
            .join(Signal, Order.signal_id == Signal.id)
            .where(*conditions)
            .order_by(Order.created_at)
        )
        rows = result.all()

    trades: list[FilledTrade] = []
    for order, signal in rows:
        price = float(order.avg_price)
        gross = _signed_gross(signal.side, order.qty, price)
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
