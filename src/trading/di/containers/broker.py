from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from trading.broker.api import Broker, BrokerStream
from trading.broker.service.paper_broker import AbstractPriceStore, PaperBroker
from trading.broker.service.zerodha.broker import ZerodhaBroker
from trading.broker.service.zerodha.kite_client import KiteClient
from trading.config.settings import Settings
from trading.monitoring.api.interfaces import AbstractFailedDispatchStore

logger = logging.getLogger(__name__)


@dataclass
class BrokerDeps:
    """Broker and streaming -- isolated so a future caller can substitute a
    fake kite_client/broker without touching infrastructure construction."""

    kite_client: KiteClient
    broker: Broker
    broker_stream: BrokerStream


def build_broker(
    settings: Settings,
    price_store: AbstractPriceStore,
    failed_dispatch: AbstractFailedDispatchStore | None = None,
) -> BrokerDeps:
    kite_client = KiteClient(settings.zerodha_api_key)

    real_broker = ZerodhaBroker(kite_client, order_timeout_secs=settings.order_timeout_secs)
    broker: Broker
    if settings.paper_trading:
        logger.info("build_broker: paper trading mode enabled")
        postback_url = f"http://{settings.dashboard_host}:{settings.dashboard_port}/api/postback"
        http_client = httpx.AsyncClient()
        broker = PaperBroker(
            real_broker,
            price_store,
            postback_url=postback_url,
            http_client=http_client,
            failed_dispatch=failed_dispatch,
        )
    else:
        broker = real_broker

    from trading.broker.service.zerodha.stream import ZerodhaStream

    broker_stream = ZerodhaStream(kite_client)

    return BrokerDeps(kite_client=kite_client, broker=broker, broker_stream=broker_stream)
