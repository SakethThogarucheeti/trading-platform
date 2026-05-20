"""FIFO matched-pair P&L computation with Indian equity trading cost model."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from trading.core.models import Signal
from trading.core.schemas import OrderStatus


@dataclass(frozen=True)
class TradeCosts:
    """
    Indian equity intraday trading cost model (Zerodha defaults).

    All rates are applied per fill leg.  Intraday positions are assumed
    throughout — delivery rates are higher and not modelled here.

    STT:          0.025% on the sell leg notional only (intraday)
    Brokerage:    min(₹20, 0.03% of notional) per order
    Exchange txn: 0.00345% of notional (NSE intraday)
    SEBI charges: 0.0001% of notional
    GST:          18% on (brokerage + exchange txn + SEBI)
    Stamp duty:   0.003% on buy notional only
    Slippage:     configurable, applied symmetrically on both legs
    """

    stt_pct: float = 0.025 / 100         # sell leg only
    brokerage_per_order: float = 20.0     # ₹20 flat or 0.03%, whichever is lower
    brokerage_pct: float = 0.03 / 100
    exchange_txn_pct: float = 0.00345 / 100
    sebi_pct: float = 0.0001 / 100
    gst_rate: float = 0.18
    stamp_pct: float = 0.003 / 100       # buy leg only
    slippage_pct: float = 0.05 / 100     # applied to both legs

    def cost_for_fill(self, side: str, qty: int, price: float) -> float:
        """Return total cost (always positive) for a single fill leg."""
        notional = qty * price

        brokerage = min(self.brokerage_per_order, notional * self.brokerage_pct)
        exchange = notional * self.exchange_txn_pct
        sebi = notional * self.sebi_pct
        gst = (brokerage + exchange + sebi) * self.gst_rate
        stt = notional * self.stt_pct if side == "SELL" else 0.0
        stamp = notional * self.stamp_pct if side == "BUY" else 0.0
        slippage = notional * self.slippage_pct

        return brokerage + exchange + sebi + gst + stt + stamp + slippage


DEFAULT_COSTS = TradeCosts()


def compute_pnl(
    signals: list[Signal],
    costs: TradeCosts = DEFAULT_COSTS,
) -> dict[str, dict[str, float]]:
    """
    Compute realized P&L per (strategy_id, symbol) using FIFO matching.

    Returns a dict keyed by "strategy_id::symbol" with:
      realized      — gross closed profit/loss (no costs)
      total_costs   — sum of all trading costs for matched pairs
      net_realized  — realized minus total_costs
      open_qty      — net open quantity (positive = long, negative = short)
      open_avg      — average price of the open position (0 if flat)
    """
    fills: dict[tuple[str, str], list[tuple[str, int, float]]] = defaultdict(list)

    for sig in signals:
        for order in sig.orders:
            if order.status != OrderStatus.FILLED.value:
                continue
            fills[(sig.strategy_id, sig.symbol)].append(
                (sig.side, order.qty, float(order.avg_price))
            )

    results: dict[str, dict[str, float]] = {}
    for (strategy_id, symbol), trades in fills.items():
        long_queue: list[tuple[int, float]] = []
        short_queue: list[tuple[int, float]] = []
        realized = 0.0
        total_costs = 0.0

        for side, qty, price in trades:
            total_costs += costs.cost_for_fill(side, qty, price)

            if side == "BUY":
                remaining = qty
                while remaining > 0 and short_queue:
                    short_qty, short_price = short_queue[0]
                    matched = min(remaining, short_qty)
                    realized += matched * (short_price - price)
                    remaining -= matched
                    if matched == short_qty:
                        short_queue.pop(0)
                    else:
                        short_queue[0] = (short_qty - matched, short_price)
                if remaining > 0:
                    long_queue.append((remaining, price))
            else:  # SELL
                remaining = qty
                while remaining > 0 and long_queue:
                    long_qty, long_price = long_queue[0]
                    matched = min(remaining, long_qty)
                    realized += matched * (price - long_price)
                    remaining -= matched
                    if matched == long_qty:
                        long_queue.pop(0)
                    else:
                        long_queue[0] = (long_qty - matched, long_price)
                if remaining > 0:
                    short_queue.append((remaining, price))

        open_qty = sum(q for q, _ in long_queue) - sum(q for q, _ in short_queue)
        open_avg = (
            sum(q * p for q, p in long_queue) / sum(q for q, _ in long_queue)
            if long_queue
            else 0.0
        )
        results[f"{strategy_id}::{symbol}"] = {
            "realized": realized,
            "total_costs": total_costs,
            "net_realized": realized - total_costs,
            "open_qty": float(open_qty),
            "open_avg": open_avg,
        }

    return results
