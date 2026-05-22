from __future__ import annotations

import logging

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]

from trading.broker.base.broker import Broker
from trading.broker.base.broker_stream import BrokerStream
from trading.broker.paper_broker import PaperBroker
from trading.broker.zerodha.broker import ZerodhaBroker
from trading.broker.zerodha.kite_client import KiteClient
from trading.config.settings import Settings

logger = logging.getLogger(__name__)


class BrokerProvider(Provider):
    """
    Broker and streaming — isolated so a MockBrokerProvider can replace
    this entire provider in tests without touching infrastructure.
    """

    scope = Scope.APP

    @provide
    def kite_client(self, settings: Settings) -> KiteClient:
        return KiteClient(settings.zerodha_api_key)

    @provide
    def broker(self, client: KiteClient, settings: Settings) -> Broker:
        real_broker = ZerodhaBroker(client, order_timeout_secs=settings.order_timeout_secs)
        if settings.paper_trading:
            logger.info("BrokerProvider: paper trading mode enabled")
            return PaperBroker(real_broker)
        return real_broker

    @provide
    def broker_stream(self, client: KiteClient) -> BrokerStream:
        from trading.broker.zerodha.stream import ZerodhaStream

        return ZerodhaStream(client)
