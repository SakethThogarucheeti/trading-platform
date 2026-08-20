"""Tests for broker/paper_broker.py — PriceStore and PaperBroker"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import polars as pl
import pytest

from trading.broker.service.broker import Broker
from trading.broker.service.paper_broker import AbstractPriceStore, PaperBroker, PriceStore
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
# PaperBroker helpers
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

    async def place_order(self, symbol, side, qty, order_type, limit_price=None, instrument_type="EQUITY", tick_log_id=0) -> str:  # type: ignore[override]
        return "REAL_ORDER_001"


_POSTBACK_URL = "http://localhost:8081/api/postback"


def _make_paper_broker(
    prices: dict[str, float] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PaperBroker:
    ps = PriceStore()
    for sym, price in (prices or {}).items():
        ps.update(sym, price)
    client = httpx.AsyncClient(transport=transport or httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})))
    # fill_delay_secs=0: the real default defers the postback to a background
    # task (see paper_broker.py) so it can't race OrderExecutor's own DB
    # write; tests instead await `_flush_background_tasks` to observe it.
    return PaperBroker(_FakeBroker(), price_store=ps, postback_url=_POSTBACK_URL, http_client=client, fill_delay_secs=0)


async def _flush_background_tasks() -> None:
    """
    Let PaperBroker's fire()-scheduled simulated fill run to completion.

    Filters strictly by fire()'s task name ("trading.fire") rather than
    "every task not already known" — asyncio.all_tasks() also surfaces
    long-lived service tasks (e.g. the cashews in-memory cache's periodic
    expiry sweep, spawned lazily on first cache use deep in this call chain)
    that loop forever and are never meant to be awaited to completion;
    gathering one of those hangs the test forever. return_exceptions=True
    mirrors fire()'s own contract — a failed background task logs and does
    not propagate.
    """
    for _ in range(10):
        pending = [t for t in asyncio.all_tasks() if t.get_name() == "trading.fire" and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Basic broker delegation
# ---------------------------------------------------------------------------


def test_paper_broker_get_instruments_delegates() -> None:
    pb = _make_paper_broker()
    df = pb.get_instruments()
    assert "symbol" in df.columns


def test_paper_broker_get_ohlc_delegates() -> None:
    pb = _make_paper_broker()
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 2, tzinfo=UTC)
    df = pb.get_ohlc("INFY", "1min", start, end)
    assert not df.is_empty()


async def test_paper_broker_place_order_returns_paper_id() -> None:
    pb = _make_paper_broker(prices={"INFY": 1500.0})
    order_id = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    assert order_id.startswith("PAPER_")


async def test_paper_broker_does_not_delegate_place_order() -> None:
    """PaperBroker.place_order must NOT call the real broker."""
    pb = _make_paper_broker(prices={"INFY": 1500.0})
    order_id = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    assert not order_id.startswith("REAL_")


async def test_paper_broker_place_order_unique_ids() -> None:
    pb = _make_paper_broker(prices={"INFY": 1500.0})
    id1 = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    id2 = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    assert id1 != id2


# ---------------------------------------------------------------------------
# Fill simulation via HTTP postback
# ---------------------------------------------------------------------------


async def test_paper_broker_posts_fill_to_postback_url() -> None:
    """place_order POSTs a COMPLETE fill payload to the postback URL."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    pb = _make_paper_broker(prices={"INFY": 1500.0}, transport=httpx.MockTransport(handler))
    returned_id = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET, instrument_type="EQUITY", tick_log_id=42)
    await _flush_background_tasks()

    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == _POSTBACK_URL
    body = json.loads(req.content)
    assert body["status"] == "COMPLETE"
    assert body["order_id"] == returned_id
    assert float(body["average_price"]) == pytest.approx(1500.0, rel=0.01)
    assert int(body["filled_quantity"]) == 10
    assert body["tradingsymbol"] == "INFY"
    assert body["instrument_type"] == "EQUITY"
    assert body["transaction_type"] == Side.BUY.value
    assert int(body["tick_log_id"]) == 42


async def test_paper_broker_no_post_when_price_unknown() -> None:
    """place_order skips the postback when no price is known for the symbol."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    pb = _make_paper_broker(prices={}, transport=httpx.MockTransport(handler))
    returned_id = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    await _flush_background_tasks()

    assert len(captured) == 0
    assert returned_id.startswith("PAPER_")


async def test_paper_broker_place_order_returns_before_postback_completes() -> None:
    """
    place_order must return the order id immediately, without waiting on the
    postback — otherwise OrderExecutor can't persist kite_order_id before the
    simulated fill's postback tries to look it up, and every paper fill is lost.
    """
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"ok": True})

    pb = _make_paper_broker(prices={"INFY": 1500.0}, transport=httpx.MockTransport(handler))
    order_id = await asyncio.wait_for(
        pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET), timeout=1.0
    )
    assert order_id.startswith("PAPER_")

    release.set()
    await _flush_background_tasks()


async def test_paper_broker_logs_postback_http_error_without_raising() -> None:
    """
    A failed simulated postback must not surface as a place_order() exception
    (it runs in a background task) — it should be logged instead so a paper
    order is never wrongly marked REJECTED due to a postback-side failure.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    pb = _make_paper_broker(prices={"INFY": 1500.0}, transport=httpx.MockTransport(handler))
    order_id = await pb.place_order("INFY", Side.BUY, 10, OrderType.MARKET)
    assert order_id.startswith("PAPER_")

    await _flush_background_tasks()


# ---------------------------------------------------------------------------
# PriceStore slippage
# ---------------------------------------------------------------------------


def test_price_store_fill_price_returns_none_when_no_price_set() -> None:
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
