"""Tests for broker/paper_broker.py — PriceStore and PaperBroker"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from trading.broker.base.broker import Broker
from trading.broker.paper_broker import AbstractPriceStore, PaperBroker, PriceStore
from trading.core.schemas import OrderType, Side

# ---------------------------------------------------------------------------
# PriceStore
# ---------------------------------------------------------------------------


def test_price_store_get_unknown_symbol_returns_none() -> None:
    ps = PriceStore()
    assert ps.get("INFY") is None


def test_price_store_update_and_get() -> None:
    ps = PriceStore()
    ps.update("INFY", 1500.0)
    assert ps.get("INFY") == 1500.0


def test_price_store_update_overwrites() -> None:
    ps = PriceStore()
    ps.update("INFY", 1500.0)
    ps.update("INFY", 1600.0)
    assert ps.get("INFY") == 1600.0


def test_price_store_multiple_symbols_independent() -> None:
    ps = PriceStore()
    ps.update("INFY", 1500.0)
    ps.update("TCS", 3000.0)
    assert ps.get("INFY") == 1500.0
    assert ps.get("TCS") == 3000.0


def test_abstract_price_store_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        AbstractPriceStore()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# PaperBroker
# ---------------------------------------------------------------------------


class _FakeBroker(Broker):
    def get_instruments(self) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["INFY"]})

    def get_ohlc(self, symbol, interval, start, end) -> pl.DataFrame:  # type: ignore[override]
        return pl.DataFrame(
            {
                "date": [start],
                "open": [100.0],
                "high": [110.0],
                "low": [90.0],
                "close": [105.0],
                "volume": [1000],
            }
        )

    async def place_order(self, symbol, side, qty, order_type, limit_price=None) -> str:  # type: ignore[override]
        return "REAL_ORDER_001"


def test_paper_broker_get_instruments_delegates() -> None:
    pb = PaperBroker(_FakeBroker())
    df = pb.get_instruments()
    assert "symbol" in df.columns


def test_paper_broker_get_ohlc_delegates() -> None:
    pb = PaperBroker(_FakeBroker())
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 2, tzinfo=UTC)
    df = pb.get_ohlc("INFY", "1min", start, end)
    assert not df.is_empty()


async def test_paper_broker_place_order_returns_paper_id() -> None:
    pb = PaperBroker(_FakeBroker())
    order_id = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    assert order_id.startswith("PAPER_")


async def test_paper_broker_does_not_delegate_place_order() -> None:
    """PaperBroker.place_order must NOT call the real broker."""
    pb = PaperBroker(_FakeBroker())
    order_id = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    # The real broker would return "REAL_ORDER_001"; paper returns PAPER_*
    assert not order_id.startswith("REAL_")


async def test_paper_broker_place_order_unique_ids() -> None:
    pb = PaperBroker(_FakeBroker())
    id1 = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    id2 = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    assert id1 != id2


def test_price_store_fill_price_returns_none_when_no_price_set() -> None:
    """Covers lines 71-76: fill_price() returns None when get() returns None."""
    ps = PriceStore()
    result = ps.fill_price("INFY", Side.BUY)
    assert result is None


def test_price_store_fill_price_buy_adds_slippage() -> None:
    ps = PriceStore(slippage_pct=0.001)
    ps.update("INFY", 1000.0)
    result = ps.fill_price("INFY", Side.BUY)
    assert result is not None
    assert result > 1000.0


def test_price_store_fill_price_sell_subtracts_slippage() -> None:
    ps = PriceStore(slippage_pct=0.001)
    ps.update("INFY", 1000.0)
    result = ps.fill_price("INFY", Side.SELL)
    assert result is not None
    assert result < 1000.0
