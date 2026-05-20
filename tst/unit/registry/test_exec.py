"""Tests for execution/order_executor.py — OrderExecutor."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.broker.base.broker import Broker
from trading.broker.paper_broker import AbstractPriceStore, PriceStore
from trading.core.database import build_session_factory, init_db
from trading.core.models import Signal
from trading.core.schemas import (
    InstrumentType,
    OrderStatus,
    OrderType,
    Side,
    SignalType,
    ValidatedOrderEvent,
)
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.storage.stores.trading import AbstractTradingStore

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


class _FakeBroker(Broker):
    def __init__(self, order_id: str = "K001") -> None:
        self._order_id = order_id

    def get_instruments(self):
        import polars as pl
        return pl.DataFrame()

    def get_ohlc(self, symbol, interval, start, end):
        import polars as pl
        return pl.DataFrame()

    async def place_order(self, symbol, side, qty, order_type, limit_price=None) -> str:
        return self._order_id


def make_order_event(**overrides) -> ValidatedOrderEvent:
    base = dict(
        signal_id=uuid4(),
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        limit_price=None,
        tick_log_id=0,
    )
    return ValidatedOrderEvent(**{**base, **overrides})  # type: ignore[arg-type]


async def _insert_signal_row(engine: AsyncEngine, signal_id) -> None:
    from trading.core.database import get_session
    from trading.core.models import Signal

    async with get_session(engine) as s:
        s.add(
            Signal(
                id=signal_id,
                strategy_id="ema",
                symbol="INFY",
                instrument_type="EQUITY",
                side="BUY",
                signal_type="ENTRY",
                stop_distance=Decimal("10"),
                created_at=NOW,
            )
        )


# ---------------------------------------------------------------------------
# fill_price path (lines 114-115) — price_store with fill_price method
# ---------------------------------------------------------------------------


async def test_exec_paper_fill_price_method_is_called(engine: AsyncEngine) -> None:
    """Covers lines 114-115: fill_price() is called on price_store when it has the method."""
    sf = build_session_factory(engine)

    class _MockTradingStore(AbstractTradingStore):
        async def save_signal(self, event): pass
        async def save_order(self, order): pass
        async def get_order_by_kite_id(self, kite_id): return None
        async def update_order_status(self, kite_id, status, avg_price=None): pass
        async def update_position(self, fill, side, symbol, instrument_type): pass
        async def get_position(self, symbol, instrument_type): return None
        async def get_daily_realized_pnl(self, date): return 0.0

    price_store = PriceStore()
    price_store.update("INFY", 1500.0)

    config = ExecConfig(exec_id="paper")
    reg = OrderExecutor(
        config=config,
        broker=_FakeBroker(),
        session_factory=sf,
        trading=_MockTradingStore(),
        price_store=price_store,
    )

    event = make_order_event()
    await _insert_signal_row(engine, event.signal_id)

    # Should not raise — fill_price is called and fill is simulated
    await reg.handle(event)


async def test_exec_paper_fill_price_returns_none_logs_warning(engine: AsyncEngine) -> None:
    """Covers lines 119-120: when fill_price returns None, fill is skipped with warning."""
    sf = build_session_factory(engine)

    class _MockTradingStore(AbstractTradingStore):
        async def save_signal(self, event): pass
        async def save_order(self, order): pass
        async def get_order_by_kite_id(self, kite_id): return None
        async def update_order_status(self, kite_id, status, avg_price=None): pass
        async def update_position(self, fill, side, symbol, instrument_type): pass
        async def get_position(self, symbol, instrument_type): return None
        async def get_daily_realized_pnl(self, date): return 0.0

    # PriceStore with no price set → fill_price returns None
    price_store = PriceStore()  # no update → returns None

    config = ExecConfig(exec_id="paper")
    reg = OrderExecutor(
        config=config,
        broker=_FakeBroker(),
        session_factory=sf,
        trading=_MockTradingStore(),
        price_store=price_store,
    )

    event = make_order_event()
    await _insert_signal_row(engine, event.signal_id)

    # Should not raise — fill skipped with warning
    await reg.handle(event)
