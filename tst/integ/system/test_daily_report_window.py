"""
/api/reports/live "day" window — real DB, full IST trading session.

Regression test for a bug found in production: get_live_report() computed
the "day" period's end boundary by taking the UTC-converted local midnight
and doing `.replace(hour=23, ...)` on the *already UTC-shifted* value. Since
00:00 IST is 18:30 UTC the previous day, that landed `end` at ~05:29:59 IST
— hours before the market even opens at 09:15 IST — silently excluding the
entire trading session from every daily report. Fixed by doing all day
boundary math in the local (IST) calendar and converting to UTC only once,
at the very end (api/routers/reports.py: get_live_report).

Seeds one FILLED order right at market open and one right at market close
on the same local day and asserts /api/reports/live?period=day includes
both — the window must span the full local trading day, not just a few
hours of it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.api.routers.reports import create_reports_router
from trading.core.clock import SimulatedClock
from trading.core.schemas import (
    InstrumentType,
    OrderType,
    Side,
    SignalType,
    ValidatedOrderEvent,
)
from trading.execution.storage.models import Order
from trading.execution.storage.store import TradingStore

IST = ZoneInfo("Asia/Kolkata")


async def _seed_filled_order(session_factory, local_ts: datetime, symbol: str, price: float) -> None:
    trading = TradingStore(session_factory)
    event = ValidatedOrderEvent(
        signal_id=uuid.uuid4(),
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        algo_name="test_algo",
        strategy_id="test_algo",
        signal_type=SignalType.ENTRY,
        quantity=1,
        order_type=OrderType.MARKET,
        tick_log_id=0,
        timestamp=local_ts.astimezone(ZoneInfo("UTC")),
    )
    await trading.save_signal(event)
    await trading.save_order(
        Order(
            id=uuid.uuid4(),
            kite_order_id=f"TEST_{uuid.uuid4().hex[:8]}",
            signal_id=event.signal_id,
            status="FILLED",
            qty=1,
            avg_price=price,
            created_at=local_ts.astimezone(ZoneInfo("UTC")),
        )
    )


def _get_live_report_endpoint(session_factory, clock):
    router = create_reports_router(
        results_dir=Path("/nonexistent"),
        session_factory=session_factory,
        clock=clock,
        cacher_factory=None,
    )
    return next(r for r in router.routes if r.path == "/api/reports/live").endpoint


async def test_day_window_includes_full_local_trading_session(engine, session_factory):
    local_day = datetime(2026, 8, 20, tzinfo=IST)
    clock = SimulatedClock()
    clock.advance(datetime(2026, 8, 20, 10, 0, tzinfo=IST).astimezone(ZoneInfo("UTC")))

    market_open_fill = local_day.replace(hour=9, minute=20)
    market_close_fill = local_day.replace(hour=15, minute=20)
    await _seed_filled_order(session_factory, market_open_fill, "INFY", 1500.0)
    await _seed_filled_order(session_factory, market_close_fill, "TCS", 3500.0)

    endpoint = _get_live_report_endpoint(session_factory, clock)
    response = await endpoint(period="day", date="2026-08-20")
    body = json.loads(response.body)

    symbols = {row["symbol"] for row in body["trades_by_symbol"]}
    assert symbols == {"INFY", "TCS"}, (
        f"expected both the market-open and market-close fills in the daily "
        f"window, got {symbols}"
    )
    assert body["order_funnel"]["filled"] == 2


async def test_day_window_excludes_fills_from_the_adjacent_day(engine, session_factory):
    clock = SimulatedClock()
    clock.advance(datetime(2026, 8, 20, 10, 0, tzinfo=IST).astimezone(ZoneInfo("UTC")))

    today_fill = datetime(2026, 8, 20, 12, 0, tzinfo=IST)
    yesterday_fill = datetime(2026, 8, 19, 12, 0, tzinfo=IST)
    await _seed_filled_order(session_factory, today_fill, "INFY", 1500.0)
    await _seed_filled_order(session_factory, yesterday_fill, "TCS", 3500.0)

    endpoint = _get_live_report_endpoint(session_factory, clock)
    response = await endpoint(period="day", date="2026-08-20")
    body = json.loads(response.body)

    symbols = {row["symbol"] for row in body["trades_by_symbol"]}
    assert symbols == {"INFY"}
