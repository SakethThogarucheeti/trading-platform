from __future__ import annotations

import logging

import httpx
from dependency_injector import containers, providers

from trading.broker.api import Broker, BrokerStream
from trading.broker.service.paper_broker import AbstractPriceStore, PaperBroker
from trading.broker.service.zerodha.broker import ZerodhaBroker
from trading.broker.service.zerodha.kite_client import KiteClient
from trading.config.settings import Settings

logger = logging.getLogger(__name__)


def _kite_client(settings: Settings) -> KiteClient:
    return KiteClient(settings.zerodha_api_key)


def _broker(client: KiteClient, settings: Settings, price_store: AbstractPriceStore) -> Broker:
    real_broker = ZerodhaBroker(client, order_timeout_secs=settings.order_timeout_secs)
    if settings.paper_trading:
        logger.info("BrokerContainer: paper trading mode enabled")
        postback_url = f"http://{settings.dashboard_host}:{settings.dashboard_port}/api/postback"
        http_client = httpx.AsyncClient()
        return PaperBroker(
            real_broker, price_store, postback_url=postback_url, http_client=http_client
        )
    return real_broker


def _broker_stream(client: KiteClient) -> BrokerStream:
    from trading.broker.service.zerodha.stream import ZerodhaStream

    return ZerodhaStream(client)


class BrokerContainer(containers.DeclarativeContainer):
    """
    Broker and streaming -- isolated so tests can override kite_client/broker
    without touching infrastructure.
    """

    settings = providers.Dependency(instance_of=Settings)
    price_store = providers.Dependency(instance_of=AbstractPriceStore)

    kite_client = providers.Singleton(_kite_client, settings=settings)

    broker = providers.Singleton(
        _broker, client=kite_client, settings=settings, price_store=price_store
    )

    broker_stream = providers.Singleton(_broker_stream, client=kite_client)
