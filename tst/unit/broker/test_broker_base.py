"""Tests for broker/base/broker.py — Broker ABC."""

from __future__ import annotations

from datetime import datetime

import polars as pl

from trading.broker.base.broker import Broker
from trading.core.schemas import OrderType, Side


class _ConcreteBroker(Broker):
    """Concrete subclass that calls super() on all abstract methods."""

    def get_instruments(self) -> pl.DataFrame:
        super().get_instruments()  # type: ignore[misc]  # covers line 16 (pass body)
        return pl.DataFrame()

    def get_ohlc(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        return pl.DataFrame()

    async def place_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        limit_price: float | None = None,
        instrument_type: str = "EQUITY",
        tick_log_id: int = 0,
    ) -> str:
        await super().place_order(symbol, side, qty, order_type, limit_price)  # covers line 26 (...)
        return "TEST_ORDER"


def test_broker_get_instruments_via_super_does_not_raise() -> None:
    """Covers line 16: abstract get_instruments() body (pass) is callable via super()."""
    broker = _ConcreteBroker()
    result = broker.get_instruments()
    assert isinstance(result, pl.DataFrame)


async def test_broker_place_order_via_super_does_not_raise() -> None:
    """Covers line 26: abstract place_order() body (...) is callable via super()."""
    broker = _ConcreteBroker()
    result = await broker.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    assert result == "TEST_ORDER"
