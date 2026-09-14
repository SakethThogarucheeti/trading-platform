"""Tests for broker/service/zerodha/broker.py — ZerodhaBroker.place_order tag forwarding (#31)."""

from __future__ import annotations

from unittest.mock import MagicMock

from trading.broker.service.zerodha.broker import ZerodhaBroker
from trading.broker.service.zerodha.kite_client import KiteClient
from trading.core.schemas import OrderType, Side


def make_broker(kite_client: KiteClient) -> ZerodhaBroker:
    return ZerodhaBroker(kite_client, order_timeout_secs=5.0)


def make_kite_client(place_order_return: str = "KITE_001") -> KiteClient:
    client = MagicMock(spec=KiteClient)
    client.place_order.return_value = place_order_return
    return client


async def test_place_order_forwards_client_tag_to_kite_client() -> None:
    kite_client = make_kite_client()
    broker = make_broker(kite_client)

    await broker.place_order(
        symbol="INFY",
        side=Side.BUY,
        qty=10,
        order_type=OrderType.MARKET,
        client_tag="abc123",
    )

    kite_client.place_order.assert_called_once()
    _, kwargs = kite_client.place_order.call_args
    assert kwargs["tag"] == "abc123"


async def test_place_order_forwards_none_client_tag_when_omitted() -> None:
    kite_client = make_kite_client()
    broker = make_broker(kite_client)

    await broker.place_order(
        symbol="INFY",
        side=Side.SELL,
        qty=5,
        order_type=OrderType.MARKET,
    )

    _, kwargs = kite_client.place_order.call_args
    assert kwargs["tag"] is None


async def test_place_order_returns_kite_order_id() -> None:
    kite_client = make_kite_client(place_order_return="KITE_XYZ")
    broker = make_broker(kite_client)

    result = await broker.place_order(
        symbol="INFY", side=Side.BUY, qty=1, order_type=OrderType.MARKET, client_tag="t1"
    )

    assert result == "KITE_XYZ"


def test_kite_client_orders_delegates_to_underlying_kite() -> None:
    client = KiteClient(api_key="fake")
    raw_orders = [{"order_id": "KITE_1", "tag": "abc", "status": "COMPLETE"}]
    client._kite.orders = MagicMock(return_value=raw_orders)  # type: ignore[attr-defined]

    result = client.orders()

    assert list(result) == raw_orders
