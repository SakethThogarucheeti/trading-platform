"""Shared FIFO trade-matching core, used by both the offline P&L report
(`trading.reports.pnl`) and the live incremental tracker
(`trading.execution.service.position_accountant`) so both compute realized
P&L the same way.
"""

from __future__ import annotations


def match_against(
    opposing_queue: list[tuple[int, float]],
    qty: int,
    price: float,
    sign: float,
) -> tuple[float, int]:
    """
    Drain `opposing_queue` FIFO against an incoming fill of `qty` @ `price`.

    `sign` is -1 for an incoming BUY matching against short_queue (profit =
    short_price - price) and +1 for an incoming SELL matching against
    long_queue (profit = price - long_price) — i.e. profit = sign * (price - queue_price).
    Returns (realized_pnl_from_matches, qty_remaining_after_matching).
    """
    realized = 0.0
    remaining = qty
    while remaining > 0 and opposing_queue:
        queue_qty, queue_price = opposing_queue[0]
        matched = min(remaining, queue_qty)
        realized += sign * matched * (price - queue_price)
        remaining -= matched
        if matched == queue_qty:
            opposing_queue.pop(0)
        else:
            opposing_queue[0] = (queue_qty - matched, queue_price)
    return realized, remaining
