from __future__ import annotations

import logging

from dependency_injector import containers, providers
from quantindicators.polars_store import PolarsStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.app.faust_app import build_faust_app
from trading.broker.api import Broker
from trading.broker.service.paper_broker import AbstractPriceStore
from trading.candles.api import (
    CandleAggregatorComponent,
    CandleConfig,
    CandleDataStore,
    CandlePersister,
    HistoricalDataService,
    Instrument,
    SymbolConfig,
)
from trading.candles.service.aggregator import CandleAggregator
from trading.config.settings import AlgoSettings, Settings
from trading.core.lifecycle.runtime import AbstractRuntime, Runtime
from trading.core.schemas import InstrumentType
from trading.di.providers.algo_pipeline import AlgoPipelineFactory, SharedAlgoDeps
from trading.execution.storage.store import TradingStore
from trading.monitoring.service.heartbeat import HeartbeatMonitor
from trading.monitoring.service.scheduler import Scheduler
from trading.monitoring.storage.store import HeartbeatStore
from trading.storage.cache import CacherFactory
from trading.strategy.storage.store import ChartStore, ConfigStore
from trading.tick_ingest.storage.store import AuditStore
from trading.worker.circuit_breaker_redis import RedisCircuitBreaker
from trading.worker.tick_agent import TickAgentComponent

logger = logging.getLogger(__name__)


def _resolve_algo(settings: Settings, algo_name: str, instrument_type_map: dict[str, str]) -> AlgoSettings:
    for algo in settings.algos:
        if algo.name == algo_name:
            if settings.paper_trading:
                return algo.model_copy(update={"execution_engine_id": "paper"})
            return algo
    raise RuntimeError(
        f"Worker: algo {algo_name!r} not found in settings. "
        f"Available: {[a.name for a in settings.algos]}"
    )


def _build_heartbeat(
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


async def _worker_runtime(
    algo_name: str,
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

    algo = _resolve_algo(settings, algo_name, instrument_type_map)

    paper_price_store = price_store if settings.paper_trading else None
    polars_store = PolarsStore()

    intervals = algo.candle_intervals or settings.candle_intervals

    circuit_breaker = RedisCircuitBreaker(redis)

    candle_config = CandleConfig(
        instruments=instruments,
        intervals=intervals,
        warmup_count=settings.warmup_candles,
    )
    candle_aggregator = CandleAggregator(
        config=candle_config,
        candle_logger=CandlePersister(candle_data_store, audit),
    )
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
    historical_data_service = HistoricalDataService(broker=broker, candle_store=candle_data_store)
    candle_aggregator_component = CandleAggregatorComponent(
        candle_aggregator=candle_aggregator,
        historical_data_service=historical_data_service,
        symbols=algo_symbol_configs,
        intervals=intervals,
        warmup_count=settings.warmup_candles,
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

    faust_app = build_faust_app(f"worker-{algo_name}", settings)
    tick_agent = TickAgentComponent(
        app=faust_app,
        tokens=tokens,
        tick_pipeline=tick_pipeline,
        circuit_breaker=circuit_breaker,
        token_symbol=token_symbol_for_algo,
        price_store=paper_price_store,
    )

    heartbeat_monitor = _build_heartbeat(heartbeat_store, sf, settings, algo.name)

    logger.info(
        "WorkerComponentContainer: algo=%r strategy=%r instruments=%d",
        algo.name,
        algo.strategy_id,
        len(algo.instruments),
    )

    return Runtime([tick_agent, candle_aggregator_component, heartbeat_monitor])


def _worker_scheduler(settings: Settings, runtime: AbstractRuntime) -> Scheduler:
    return Scheduler(settings, on_market_open=runtime.start, on_market_close=runtime.stop)


class WorkerComponentContainer(containers.DeclarativeContainer):
    """
    Builds the worker-process Runtime for a single named algo.

    Mirrors ComponentContainer but:
    - Uses TickAgentComponent (a faust-streaming agent consuming the shared
      Kafka `ticks` topic) instead of KiteIngestor (WebSocket)
    - Uses RedisCircuitBreaker instead of the in-memory CircuitBreaker
    - Activates only the algo whose name matches `algo_name`
    - Does NOT run migrations or instrument sync (those belong to the ingestor)
    """

    algo_name = providers.Dependency(instance_of=str)
    settings = providers.Dependency(instance_of=Settings)
    broker = providers.Dependency(instance_of=Broker)
    sf = providers.Dependency()  # async_sessionmaker[AsyncSession]
    redis = providers.Dependency()
    candle_data_store = providers.Dependency(instance_of=CandleDataStore)
    trading = providers.Dependency(instance_of=TradingStore)
    audit = providers.Dependency(instance_of=AuditStore)
    chart = providers.Dependency(instance_of=ChartStore)
    config_store = providers.Dependency(instance_of=ConfigStore)
    price_store = providers.Dependency(instance_of=AbstractPriceStore)
    heartbeat_store = providers.Dependency(instance_of=HeartbeatStore)
    cacher_factory = providers.Dependency(instance_of=CacherFactory)

    runtime = providers.Resource(
        _worker_runtime,
        algo_name=algo_name,
        sf=sf,
        broker=broker,
        candle_data_store=candle_data_store,
        audit=audit,
        chart=chart,
        config_store=config_store,
        trading=trading,
        heartbeat_store=heartbeat_store,
        price_store=price_store,
        settings=settings,
        redis=redis,
        cacher_factory=cacher_factory,
    )

    scheduler = providers.Singleton(_worker_scheduler, settings=settings, runtime=runtime)
