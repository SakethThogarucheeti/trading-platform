from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.api.app import build_app
from trading.broker.zerodha.kite_client import KiteClient
from trading.candles.historical_data_service import HistoricalDataService
from trading.core.clock import SYSTEM_CLOCK, Clock
from trading.core.lifecycle.component import Component
from trading.storage.cache import CacherFactory
from trading.tick_ingest.kite_ingestor import KiteIngestor

logger = logging.getLogger(__name__)


class ApiServer(Component):
    """
    Lifecycle wrapper that runs the FastAPI app inside the Runtime.

    Starts last in the component list so all other components are running
    before the server begins serving requests. Stops cleanly when the
    runtime shuts down by signalling uvicorn's ``should_exit`` flag.

    Access the API at http://<host>:<port>/ (default 127.0.0.1:8081).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        host: str = "127.0.0.1",
        port: int = 8081,
        clock: Clock = SYSTEM_CLOCK,
        candle_intervals: list[str] | None = None,
        zerodha_api_key: str = "",
        zerodha_api_secret: str = "",
        token_secret_key: str = "",
        kite_client: KiteClient | None = None,
        kite_ingestor: KiteIngestor | None = None,
        cacher_factory: CacherFactory | None = None,
        historical_data_service: HistoricalDataService | None = None,
        heartbeat_stale_secs: int = 30,
    ) -> None:
        super().__init__(name="api")
        self._session_factory = session_factory
        self._host = host
        self._port = port
        self._clock = clock
        self._candle_intervals = candle_intervals
        self._zerodha_api_key = zerodha_api_key
        self._zerodha_api_secret = zerodha_api_secret
        self._token_secret_key = token_secret_key
        self._kite_client = kite_client
        self._kite_ingestor = kite_ingestor
        self._cacher_factory = cacher_factory
        self._historical_data_service = historical_data_service
        self._heartbeat_stale_secs = heartbeat_stale_secs
        self._server: object | None = None  # uvicorn.Server, set in _setup

    async def _setup(self) -> None:
        import socket

        import uvicorn

        app = build_app(
            self._session_factory,
            self._clock,
            candle_intervals=self._candle_intervals,
            zerodha_api_key=self._zerodha_api_key,
            zerodha_api_secret=self._zerodha_api_secret,
            token_secret_key=self._token_secret_key,
            kite_client=self._kite_client,
            kite_ingestor=self._kite_ingestor,
            cacher_factory=self._cacher_factory,
            historical_data_service=self._historical_data_service,
            heartbeat_stale_secs=self._heartbeat_stale_secs,
        )
        config = uvicorn.Config(
            app=app,
            host=self._host,
            port=self._port,
            log_level="warning",  # keep uvicorn quiet; our logger handles app logs
            access_log=False,
        )
        config.load()
        # Allow immediate rebind after restart — avoids "port in use" on TIME_WAIT
        config.socket_options = [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]  # type: ignore[attr-defined]
        self._server = uvicorn.Server(config)
        logger.info("ApiServer: ready on http://%s:%d", self._host, self._port)

    async def _run(self) -> None:
        await self._server.serve()  # type: ignore[union-attr]

    async def _teardown(self) -> None:
        if self._server is not None:
            self._server.should_exit = True  # type: ignore[union-attr]
            logger.info("ApiServer: shutdown requested")
