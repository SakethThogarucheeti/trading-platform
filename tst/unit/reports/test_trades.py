"""Tests for reports/trades.py — fetch_filled_trades.

Regression coverage for trading-platform#89: `gross` must be FIFO-matched
realized P&L, not signed cash flow -- an opening fill contributes 0, a
closing fill carries the profit/loss matched against what it closed. Before
this fix, `_signed_gross` reported every SELL as pure positive cash flow and
every BUY as pure negative cash flow regardless of whether it opened or
closed a position, which is what the dashboard's "realized P&L" numbers were
silently built on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import build_session_factory, get_session, init_db
from trading.core.clock import SimulatedClock
from trading.execution.storage.models import Order
from trading.reports.trades import fetch_filled_trades, summarize, summarize_by_algo
from trading.strategy.storage.models import Signal

IST_MIDNIGHT_UTC = datetime(2025, 1, 5, 18, 30, tzinfo=UTC)  # 2025-01-06 00:00 IST


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def _clock_at(when: datetime) -> SimulatedClock:
    clock = SimulatedClock()
    clock.advance(when)
    return clock


async def _add_fill(
    engine: AsyncEngine,
    *,
    side: str,
    qty: int,
    price: str,
    created_at: datetime,
    symbol: str = "INFY",
    algo_name: str | None = "ema_crossover",
) -> None:
    async with get_session(engine) as s:
        signal = Signal(
            id=uuid4(),
            strategy_id="ema",
            algo_name=algo_name,
            symbol=symbol,
            instrument_type="EQUITY",
            side=side,
            signal_type="ENTRY",
            stop_distance=Decimal("10"),
            created_at=created_at,
        )
        s.add(signal)
        await s.flush()
        s.add(
            Order(
                id=uuid4(),
                kite_order_id=f"KITE_{uuid4().hex[:8]}",
                signal_id=signal.id,
                status="FILLED",
                qty=qty,
                avg_price=Decimal(price),
                created_at=created_at,
            )
        )


async def test_opening_fill_has_zero_gross(engine: AsyncEngine) -> None:
    """A lone BUY with nothing to close against is an open position, not a
    realized gain -- gross must be 0, not the old signed cash-flow value."""
    day = datetime(2025, 1, 6, 9, 15, tzinfo=UTC)
    await _add_fill(engine, side="BUY", qty=10, price="100.00", created_at=day)

    sf = build_session_factory(engine)
    trades = await fetch_filled_trades(
        sf, start=IST_MIDNIGHT_UTC, end=day + timedelta(hours=1), clock=_clock_at(day)
    )

    assert len(trades) == 1
    assert trades[0].gross == pytest.approx(0.0)


async def test_closing_fill_carries_fifo_matched_realized_pnl(engine: AsyncEngine) -> None:
    """BUY 10 @ 100 then SELL 10 @ 120: the SELL's gross is the matched
    profit (10 * 20 = 200), not its signed notional (+1200)."""
    buy_at = datetime(2025, 1, 6, 9, 15, tzinfo=UTC)
    sell_at = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)
    await _add_fill(engine, side="BUY", qty=10, price="100.00", created_at=buy_at)
    await _add_fill(engine, side="SELL", qty=10, price="120.00", created_at=sell_at)

    sf = build_session_factory(engine)
    trades = await fetch_filled_trades(
        sf, start=IST_MIDNIGHT_UTC, end=sell_at + timedelta(minutes=1), clock=_clock_at(sell_at)
    )

    assert len(trades) == 2
    buy_trade, sell_trade = trades
    assert buy_trade.gross == pytest.approx(0.0)
    assert sell_trade.gross == pytest.approx(200.0)
    assert sell_trade.net == pytest.approx(200.0 - sell_trade.cost)


async def test_seeding_fill_before_window_start_excluded_from_results(engine: AsyncEngine) -> None:
    """A closing fill just inside [start, end] must still match against an
    opening fill from earlier the same local trading day, but that earlier
    seeding fill itself must not appear in the returned list."""
    buy_at = datetime(2025, 1, 6, 4, 0, tzinfo=UTC)  # 09:30 IST, before the window
    sell_at = datetime(2025, 1, 6, 10, 0, tzinfo=UTC)  # 15:30 IST, inside the window
    window_start = datetime(2025, 1, 6, 6, 0, tzinfo=UTC)  # 11:30 IST
    await _add_fill(engine, side="BUY", qty=5, price="50.00", created_at=buy_at)
    await _add_fill(engine, side="SELL", qty=5, price="70.00", created_at=sell_at)

    sf = build_session_factory(engine)
    trades = await fetch_filled_trades(
        sf, start=window_start, end=sell_at + timedelta(minutes=1), clock=_clock_at(sell_at)
    )

    assert len(trades) == 1
    assert trades[0].side == "SELL"
    # Matched against the earlier same-day BUY even though that BUY is outside [start, end].
    assert trades[0].gross == pytest.approx(5 * (70.0 - 50.0))


async def test_prior_trading_day_fill_not_used_to_seed(engine: AsyncEngine) -> None:
    """A fill from a previous local trading day must not seed today's FIFO
    queue -- EOD square-off (#96) guarantees positions are flat at every
    local-day boundary, so a same-symbol BUY from yesterday is unrelated."""
    yesterday_buy = datetime(2025, 1, 5, 9, 15, tzinfo=UTC)
    today_sell = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)
    await _add_fill(engine, side="BUY", qty=10, price="100.00", created_at=yesterday_buy)
    await _add_fill(engine, side="SELL", qty=10, price="120.00", created_at=today_sell)

    sf = build_session_factory(engine)
    trades = await fetch_filled_trades(
        sf,
        start=IST_MIDNIGHT_UTC,
        end=today_sell + timedelta(minutes=1),
        clock=_clock_at(today_sell),
    )

    assert len(trades) == 1
    # No opposing queue entry survives from yesterday, so this SELL opens a
    # fresh short rather than matching -- 0 realized, not the old cash-flow +1200.
    assert trades[0].gross == pytest.approx(0.0)


async def test_algo_name_filter_narrows_output_not_fifo_seeding(engine: AsyncEngine) -> None:
    """Filtering to one algo must not change the FIFO matching of a fill
    from a different algo on the same symbol -- positions are per-symbol,
    not per-algo (mirrors PositionAccountant's live accounting)."""
    buy_at = datetime(2025, 1, 6, 9, 15, tzinfo=UTC)
    sell_at = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)
    await _add_fill(
        engine, side="BUY", qty=10, price="100.00", created_at=buy_at, algo_name="algo_a"
    )
    await _add_fill(
        engine, side="SELL", qty=10, price="120.00", created_at=sell_at, algo_name="algo_b"
    )

    sf = build_session_factory(engine)
    trades = await fetch_filled_trades(
        sf,
        start=IST_MIDNIGHT_UTC,
        end=sell_at + timedelta(minutes=1),
        clock=_clock_at(sell_at),
        algo_name="algo_b",
    )

    assert len(trades) == 1
    assert trades[0].algo_name == "algo_b"
    assert trades[0].gross == pytest.approx(200.0)


async def test_summarize_and_summarize_by_algo_use_fifo_gross(engine: AsyncEngine) -> None:
    buy_at = datetime(2025, 1, 6, 9, 15, tzinfo=UTC)
    sell_at = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)
    await _add_fill(
        engine, side="BUY", qty=10, price="100.00", created_at=buy_at, algo_name="ema"
    )
    await _add_fill(
        engine, side="SELL", qty=10, price="120.00", created_at=sell_at, algo_name="ema"
    )

    sf = build_session_factory(engine)
    trades = await fetch_filled_trades(
        sf, start=IST_MIDNIGHT_UTC, end=sell_at + timedelta(minutes=1), clock=_clock_at(sell_at)
    )

    summary = summarize(trades)
    assert summary.gross == pytest.approx(200.0)

    by_algo = summarize_by_algo(trades)
    assert by_algo["ema"].gross == pytest.approx(200.0)
