"""
EOD square-off audit trail — real DB.

Regression test for a bug found in production: eod_square_off() used to
call PositionStore.update_position() directly, zeroing the position with
no Signal or Order row at all. The exit fill was invisible to /api/pnl —
today's realized P&L silently excluded the final close-out every day.

Kept intentionally small (one or two positions, no candle/tick data) so
this runs fast alongside the rest of the suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from trading.core.clock import SYSTEM_CLOCK, SimulatedClock
from trading.execution.service.eod_square_off import (
    EOD_SQUARE_OFF_NAME,
    square_off_open_positions,
)
from trading.execution.service.position_accountant import PositionAccountant
from trading.execution.storage.models import Order, Position
from trading.execution.storage.store import PositionStore, TradingStore
from trading.reports.trades import fetch_filled_trades
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.strategy.storage.models import Signal


class _FixedPriceStore:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def get(self, symbol: str) -> float | None:
        return self._prices.get(symbol)


async def _seed_position(session_factory, symbol: str, net_qty: int, avg_price: float) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Position(
                    symbol=symbol,
                    instrument_type="EQUITY",
                    net_qty=net_qty,
                    avg_price=Decimal(str(avg_price)),
                    updated_at=datetime.now(UTC),
                )
            )


def _make_accountant(session_factory) -> PositionAccountant:
    setup_cache(None)
    return PositionAccountant(
        PositionStore(session_factory), CacherFactory(ValueCache(), SYSTEM_CLOCK)
    )


async def test_square_off_creates_signal_and_order_rows(engine, session_factory):
    await _seed_position(session_factory, "INFY", net_qty=3, avg_price=1133.2664)
    trading = TradingStore(session_factory)
    accountant = _make_accountant(session_factory)
    price_store = _FixedPriceStore({"INFY": 1129.4})
    clock = SimulatedClock()
    clock.advance(datetime(2026, 8, 20, 9, 59, tzinfo=UTC))

    await square_off_open_positions(trading, accountant, price_store, clock)

    async with session_factory() as session:
        signals = (await session.execute(select(Signal))).scalars().all()
        orders = (await session.execute(select(Order))).scalars().all()

    assert len(signals) == 1
    assert signals[0].algo_name == EOD_SQUARE_OFF_NAME
    assert signals[0].side == "SELL"
    assert signals[0].symbol == "INFY"

    assert len(orders) == 1
    assert orders[0].status == "FILLED"
    assert orders[0].qty == 3
    assert orders[0].avg_price == Decimal("1129.4")
    assert orders[0].signal_id == signals[0].id


async def test_square_off_zeroes_the_position(engine, session_factory):
    await _seed_position(session_factory, "INFY", net_qty=3, avg_price=1133.2664)
    trading = TradingStore(session_factory)
    accountant = _make_accountant(session_factory)
    price_store = _FixedPriceStore({"INFY": 1129.4})
    clock = SimulatedClock()
    clock.advance(datetime(2026, 8, 20, 9, 59, tzinfo=UTC))

    await square_off_open_positions(trading, accountant, price_store, clock)

    async with session_factory() as session:
        position = await session.get(Position, {"symbol": "INFY", "instrument_type": "EQUITY"})
    assert position is not None
    assert position.net_qty == 0


async def test_square_off_exit_is_visible_in_pnl(engine, session_factory):
    """The core regression: the EOD exit fill must actually show up in the
    same query /api/pnl uses (fetch_filled_trades), not just exist as a row
    somewhere disconnected from the reporting path."""
    await _seed_position(session_factory, "INFY", net_qty=3, avg_price=1133.2664)
    trading = TradingStore(session_factory)
    accountant = _make_accountant(session_factory)
    price_store = _FixedPriceStore({"INFY": 1129.4})
    clock = SimulatedClock()
    now = datetime(2026, 8, 20, 9, 59, tzinfo=UTC)
    clock.advance(now)

    await square_off_open_positions(trading, accountant, price_store, clock)

    trades = await fetch_filled_trades(
        session_factory, start=datetime(2026, 8, 20, tzinfo=UTC), end=now
    )
    assert len(trades) == 1
    assert trades[0].symbol == "INFY"
    assert trades[0].side == "SELL"
    assert trades[0].qty == 3
    assert trades[0].avg_price == 1129.4
    # gross is a signed cash-flow leg (SELL positive, BUY negative) — this
    # system nets P&L cumulatively across entry+exit pairs, not per-leg —
    # so the important thing is that this leg is counted at all (previously
    # it was silently absent from fetch_filled_trades entirely).
    assert trades[0].gross == 1129.4 * 3


async def test_short_position_squared_off_with_buy(engine, session_factory):
    await _seed_position(session_factory, "TCS", net_qty=-2, avg_price=3500.0)
    trading = TradingStore(session_factory)
    accountant = _make_accountant(session_factory)
    price_store = _FixedPriceStore({"TCS": 3510.0})
    clock = SimulatedClock()
    clock.advance(datetime(2026, 8, 20, 9, 59, tzinfo=UTC))

    await square_off_open_positions(trading, accountant, price_store, clock)

    async with session_factory() as session:
        signals = (await session.execute(select(Signal))).scalars().all()
        position = await session.get(Position, {"symbol": "TCS", "instrument_type": "EQUITY"})

    assert len(signals) == 1
    assert signals[0].side == "BUY"
    assert position is not None
    assert position.net_qty == 0


async def test_multiple_open_positions_all_squared_off(engine, session_factory):
    await _seed_position(session_factory, "INFY", net_qty=3, avg_price=1133.0)
    await _seed_position(session_factory, "TCS", net_qty=-2, avg_price=3500.0)
    trading = TradingStore(session_factory)
    accountant = _make_accountant(session_factory)
    price_store = _FixedPriceStore({"INFY": 1129.0, "TCS": 3510.0})
    clock = SimulatedClock()
    clock.advance(datetime(2026, 8, 20, 9, 59, tzinfo=UTC))

    await square_off_open_positions(trading, accountant, price_store, clock)

    async with session_factory() as session:
        orders = (await session.execute(select(Order))).scalars().all()
    assert len(orders) == 2
    assert {o.qty for o in orders} == {3, 2}


async def test_no_open_positions_is_a_noop(engine, session_factory):
    trading = TradingStore(session_factory)
    accountant = _make_accountant(session_factory)
    price_store = _FixedPriceStore({})
    clock = SimulatedClock()
    clock.advance(datetime(2026, 8, 20, 9, 59, tzinfo=UTC))

    await square_off_open_positions(trading, accountant, price_store, clock)

    async with session_factory() as session:
        signals = (await session.execute(select(Signal))).scalars().all()
        orders = (await session.execute(select(Order))).scalars().all()
    assert signals == []
    assert orders == []
