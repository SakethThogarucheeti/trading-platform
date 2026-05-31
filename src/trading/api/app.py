from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.gzip import GZipMiddleware

from trading.api.routers._middleware import AccessLogMiddleware, RequestIdMiddleware
from trading.api.routers.algos import create_algos_router
from trading.api.routers.auth import create_auth_router
from trading.api.routers.broker import create_broker_router
from trading.api.routers.charts import create_charts_router
from trading.api.routers.data import create_data_router
from trading.api.routers.market import create_market_router
from trading.api.routers.pnl import create_pnl_router
from trading.api.routers.reports import create_reports_router
from trading.api.routers.stream import create_stream_router
from trading.broker.zerodha.kite_client import KiteClient
from trading.candles.historical_data_service import HistoricalDataService
from trading.core.clock import SYSTEM_CLOCK, Clock
from trading.execution.order_executor import OrderExecutor
from trading.storage.cache import CacherFactory
from trading.tick_ingest.kite_ingestor import KiteIngestor


def build_app(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock = SYSTEM_CLOCK,
    results_dir: Path | None = None,
    candle_intervals: list[str] | None = None,
    zerodha_api_key: str = "",
    zerodha_api_secret: str = "",
    token_secret_key: str = "",
    kite_client: KiteClient | None = None,
    kite_ingestor: KiteIngestor | None = None,
    order_executor: OrderExecutor | None = None,
    cacher_factory: CacherFactory | None = None,
    historical_data_service: HistoricalDataService | None = None,
    heartbeat_stale_secs: int = 30,
) -> FastAPI:
    app = FastAPI(title="Algo Trading Dashboard", docs_url=None, redoc_url=None)

    # Middleware stack — outermost registered last in Starlette's model,
    # so list order here is outermost-first at request time.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    _results_dir = results_dir or Path("results")
    _candle_intervals = candle_intervals or ["5min"]

    app.include_router(create_auth_router(
        session_factory, zerodha_api_key, zerodha_api_secret,
        token_secret_key, kite_client, kite_ingestor,
    ))
    app.include_router(create_market_router(session_factory, clock, heartbeat_stale_secs))
    app.include_router(create_algos_router(session_factory))
    app.include_router(create_pnl_router(session_factory, clock, cacher_factory))
    app.include_router(create_reports_router(_results_dir, session_factory, clock, cacher_factory))
    app.include_router(create_charts_router(session_factory, clock))
    app.include_router(create_stream_router(session_factory, clock))
    app.include_router(create_broker_router(order_executor))
    app.include_router(create_data_router(
        session_factory, clock, _candle_intervals, historical_data_service,
    ))

    return app
