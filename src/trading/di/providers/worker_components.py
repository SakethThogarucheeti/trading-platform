from __future__ import annotations

import logging

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from quantindicators.polars_store import PolarsStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.broker.base.broker import Broker
from trading.broker.paper_broker import AbstractPriceStore
from trading.candles.bar_accumulator import SymbolConfig
from trading.candles.candle_aggregator import (
    CandleAggregator,
    CandleAggregatorComponent,
    CandleConfig,
    CandlePersister,
)
from trading.candles.historical_data_service import HistoricalDataService
from trading.config.settings import AlgoSettings, Settings
from trading.core.lifecycle.runtime import AbstractRuntime, Runtime
from trading.core.models import Instrument
from trading.core.schemas import InstrumentType
from trading.di.providers.algo_pipeline import AlgoPipelineFactory, SharedAlgoDeps
from trading.monitoring.heartbeat import HeartbeatMonitor
from trading.monitoring.scheduler import Scheduler
from trading.storage.cache import CacherFactory
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.candle import CandleDataStore
from trading.storage.stores.chart import ChartStore
from trading.storage.stores.config import ConfigStore
from trading.storage.stores.heartbeat import HeartbeatStore
from trading.storage.stores.trading import TradingStore
from trading.worker.circuit_breaker_redis import RedisCircuitBreaker
from trading.worker.tick_subscriber import TickSubscriber

logger = logging.getLogger(__name__)


class WorkerComponentProvider(Provider):
    """
    Builds the worker-process Runtime for a single named algo.

    Mirrors ComponentProvider but:
    - Uses TickSubscriber (Redis pub/sub) instead of KiteIngestor (WebSocket)
    - Uses RedisCircuitBreaker instead of the in-memory CircuitBreaker
    - Activates only the algo whose name matches ``algo_name``
    - Does NOT run migrations or instrument sync (those belong to the ingestor)
    """

    scope = Scope.APP

    def __init__(self, algo_name: str) -> None:
        super().__init__()
        self._algo_name = algo_name

    @provide
    async def runtime(
        self,
        sf: async_sessionmaker[AsyncSession],
        broker: Broker,
        candle_data_store: CandleDataStore,
        audit: AuditStore,
        chart: ChartStore,
        config_store: ConfigStore,
        trading: TradingStore,
        heartbeat_store: HeartbeatStore,
        price_store: AbstractPriceStore,
        settings: Settings,
        redis: object,
        cacher_factory: CacherFactory,
    ) -> AbstractRuntime:
        from sqlalchemy import select

        async with sf() as session:
            instruments = list((await session.execute(select(Instrument))).scalars().all())

        instrument_type_map = {r.symbol: r.instrument_type for r in instruments}
        token_symbol: dict[int, str] = {r.token: r.symbol for r in instruments}

        algo = self._resolve_algo(settings, instrument_type_map)

        paper_price_store = price_store if settings.paper_trading else None
        polars_store = PolarsStore()

        intervals = algo.candle_intervals or settings.candle_intervals

        # Circuit breaker backed by Redis — worker never opens/closes it directly
        circuit_breaker = RedisCircuitBreaker(redis)

        # Candle aggregator (shared across TickPipeline for this worker)
        candle_config = CandleConfig(
            instruments=instruments,
            intervals=intervals,
            warmup_count=settings.warmup_candles,
        )
        candle_aggregator = CandleAggregator(
            config=candle_config,
            candle_logger=CandlePersister(candle_data_store, audit),
        )
        # Resolve instrument tokens for subscription
        algo_symbols = set(algo.instruments)
        tokens = [r.token for r in instruments if r.symbol in algo_symbols]
        token_symbol_for_algo = {t: s for t, s in token_symbol.items() if s in algo_symbols}

        algo_symbol_configs = [
            SymbolConfig(
                symbol=inst.symbol,
                instrument_token=inst.token,
                instrument_type=InstrumentType(inst.instrument_type),
            )
            for inst in instruments
            if inst.symbol in algo_symbols
        ]
        historical_data_service = HistoricalDataService(
            broker=broker, candle_store=candle_data_store
        )
        candle_aggregator_component = CandleAggregatorComponent(
            candle_aggregator=candle_aggregator,
            historical_data_service=historical_data_service,
            symbols=algo_symbol_configs,
            intervals=intervals,
            warmup_count=settings.warmup_candles,
        )

        tick_subscriber = TickSubscriber(
            redis=redis,
            tokens=tokens,
            circuit_breaker=circuit_breaker,
            token_symbol=token_symbol_for_algo,
            price_store=paper_price_store,
        )

        factory = AlgoPipelineFactory(SharedAlgoDeps(
            chart=chart,
            config_store=config_store,
            audit=audit,
            trading=trading,
            broker=broker,
            session_factory=sf,
            polars_store=polars_store,
            settings=settings,
            factory=cacher_factory,
        ))

        tick_pipeline = factory.build_pipeline(
            algo=algo,
            intervals=intervals,
            instrument_type_map=instrument_type_map,
            circuit=circuit_breaker,
            candle_registry=candle_aggregator,
        )
        await factory.seed_state(algo, intervals)

        candle_aggregator_component.add_algo_registry(tick_pipeline.signal_generator)
        tick_subscriber.add_on_tick(tick_pipeline.run)

        heartbeat_monitor = self._build_heartbeat(
            heartbeat_store, sf, settings, algo.name
        )

        logger.info(
            "WorkerComponentProvider: algo=%r strategy=%r instruments=%d",
            algo.name,
            algo.strategy_id,
            len(algo.instruments),
        )

        return Runtime([tick_subscriber, candle_aggregator_component, heartbeat_monitor])

    @provide
    def scheduler(self, settings: Settings, runtime: AbstractRuntime) -> Scheduler:
        return Scheduler(
            settings,
            on_market_open=runtime.start,
            on_market_close=runtime.stop,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_algo(
        self, settings: Settings, instrument_type_map: dict[str, str]
    ) -> AlgoSettings:
        for algo in settings.algos:
            if algo.name == self._algo_name:
                if settings.paper_trading:
                    return algo.model_copy(update={"execution_engine_id": "paper"})
                return algo
        raise RuntimeError(
            f"Worker: algo {self._algo_name!r} not found in settings. "
            f"Available: {[a.name for a in settings.algos]}"
        )

    def _build_heartbeat(
        self,
        heartbeat_store: HeartbeatStore,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
        algo_name: str,
    ) -> HeartbeatMonitor:
        from trading.api.telegram import TelegramAlerter

        alerter = TelegramAlerter(settings)

        async def _alert(module: str) -> None:
            await alerter.send_alert(
                f"Heartbeat missed: {module} is unresponsive",
                event_type=f"heartbeat:{module}",
            )

        component_name = f"worker:{algo_name}:heartbeat_monitor"
        return HeartbeatMonitor(
            heartbeat_store,
            sf,
            component_names=[component_name],
            beat_interval_secs=settings.heartbeat_interval_secs,
            timeout_secs=settings.heartbeat_timeout_secs,
            alerter=_alert,
        )
