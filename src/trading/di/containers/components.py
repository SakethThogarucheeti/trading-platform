from __future__ import annotations

import logging

from aiokafka import AIOKafkaProducer
from dependency_injector import containers, providers
from quantindicators.polars_store import PolarsStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.broker.api import Broker, BrokerStream
from trading.broker.service.paper_broker import AbstractPriceStore
from trading.broker.service.zerodha.kite_client import KiteClient
from trading.candles.api import (
    CandleAggregator,
    CandleAggregatorComponent,
    CandleConfig,
    CandleDataStore,
    CandlePersister,
    HistoricalDataService,
    Instrument,
    SymbolConfig,
)
from trading.config.settings import AlgoSettings, Settings
from trading.core.clock import Clock
from trading.core.lifecycle.runtime import AbstractRuntime, Runtime
from trading.core.messaging import AbstractCircuitBreaker
from trading.core.schemas import InstrumentType
from trading.di.providers.algo_pipeline import AlgoPipelineFactory, SharedAlgoDeps
from trading.execution.api import OrderExecutor
from trading.execution.storage.store import PositionStore, TradingStore
from trading.monitoring.service.heartbeat import HeartbeatMonitor
from trading.monitoring.service.scheduler import Scheduler
from trading.monitoring.storage.store import HeartbeatStore
from trading.storage.cache import CacherFactory
from trading.strategy.storage.store import ChartStore, ConfigStore
from trading.tick_ingest.api import (
    CircuitBreaker,
    KiteIngestor,
    TickConfig,
    TickIngestor,
    TickPublisher,
)
from trading.tick_ingest.storage.store import AuditStore

logger = logging.getLogger(__name__)


def _circuit_breaker() -> AbstractCircuitBreaker:
    return CircuitBreaker()


def _kafka_bootstrap_servers(settings: Settings) -> str:
    """aiokafka wants "host:port", not the faust-style "kafka://host:port" URL."""
    return settings.kafka_broker_url.removeprefix("kafka://")


async def _load_instruments(sf: async_sessionmaker[AsyncSession]) -> list[Instrument]:
    from sqlalchemy import select

    async with sf() as session:
        return list((await session.execute(select(Instrument))).scalars().all())


async def _tick_registry(
    stream: BrokerStream,
    audit: AuditStore,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    circuit: AbstractCircuitBreaker,
) -> TickIngestor:
    instruments = await _load_instruments(sf)
    exec_id = "paper" if settings.paper_trading else "direct"
    config = TickConfig(instruments=instruments, exec_id=exec_id)
    return TickIngestor(config=config, stream=stream, audit=audit, circuit=circuit)


async def _candle_registry(
    candle: CandleDataStore,
    audit: AuditStore,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> CandleAggregator:
    instruments = await _load_instruments(sf)
    config = CandleConfig(
        instruments=instruments,
        intervals=settings.candle_intervals,
        warmup_count=settings.warmup_candles,
    )
    return CandleAggregator(config=config, candle_logger=CandlePersister(candle, audit))


def _historical_data_service(broker: Broker, candle: CandleDataStore) -> HistoricalDataService:
    return HistoricalDataService(broker=broker, candle_store=candle)


def _heartbeat_monitor(
    heartbeat: HeartbeatStore,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> HeartbeatMonitor:
    from trading.api.telegram import TelegramAlerter

    alerter = TelegramAlerter(settings)

    async def _alert(module: str) -> None:
        await alerter.send_alert(
            f"Heartbeat missed: {module} is unresponsive",
            event_type=f"heartbeat:{module}",
        )

    return HeartbeatMonitor(
        heartbeat,
        sf,
        component_names=[],
        beat_interval_secs=settings.heartbeat_interval_secs,
        timeout_secs=settings.heartbeat_timeout_secs,
        alerter=_alert,
    )


def _normalize_algo_configs(
    settings: Settings, instrument_type_map: dict[str, str]
) -> list[AlgoSettings]:
    exec_id = "paper" if settings.paper_trading else "direct"
    algo_configs = settings.algos
    if not algo_configs:
        all_symbols = list(instrument_type_map.keys())
        return [
            AlgoSettings(
                name="default",
                instruments=all_symbols,
                broker_name="paper" if settings.paper_trading else "zerodha",
                execution_engine_id=exec_id,
                equity=settings.default_equity,
            )
        ]
    if settings.paper_trading:
        return [a.model_copy(update={"execution_engine_id": exec_id}) for a in algo_configs]
    return list(algo_configs)


class _RuntimeAssembler:
    """
    Builds the ingestor-process AbstractRuntime: wires the tick ingestor, the
    per-algo TickPipelines (one per AlgoSettings entry -- a dynamic-length
    loop driven by runtime config, not something expressed as a declarative
    provider graph), and the candle aggregator component together.

    Holds the one piece of state ComponentContainer's old ComponentProvider
    needed across two @provide methods (self._kite_ingestor, read later by
    the dashboard provider) -- kept as plain instance state here rather than
    forced into a stateless provider shape.
    """

    def __init__(self) -> None:
        self.kite_ingestor: KiteIngestor | None = None
        # Fills post back to a single shared /api/postback endpoint regardless
        # of which algo placed the order (handle_fill() is a stateless DB
        # lookup by kite_order_id), so any one algo's OrderExecutor works —
        # last one wins, consistent across single- and multi-algo configs.
        self.order_executor: OrderExecutor | None = None

    async def build_runtime(
        self,
        tick_registry: TickIngestor,
        candle_registry: CandleAggregator,
        historical_data_service: HistoricalDataService,
        heartbeat_monitor: HeartbeatMonitor,
        stream: BrokerStream,
        broker: Broker,
        trading: TradingStore,
        audit: AuditStore,
        chart: ChartStore,
        config_store: ConfigStore,
        price_store: AbstractPriceStore,
        settings: Settings,
        sf: async_sessionmaker[AsyncSession],
        redis: object,
        circuit: AbstractCircuitBreaker,
        cacher_factory: CacherFactory,
    ) -> AbstractRuntime:
        instruments = await _load_instruments(sf)
        instrument_type_map = {r.symbol: r.instrument_type for r in instruments}
        algo_configs = _normalize_algo_configs(settings, instrument_type_map)
        if settings.worker_algo_names:
            algo_configs = [a for a in algo_configs if a.name not in settings.worker_algo_names]

        paper_price_store = price_store if settings.paper_trading else None
        polars_store = PolarsStore()

        producer = AIOKafkaProducer(bootstrap_servers=_kafka_bootstrap_servers(settings))
        tick_publisher = TickPublisher(producer, redis)

        ingestor = KiteIngestor(
            stream=stream,
            tick_registry=tick_registry,
            circuit=circuit,
            circuit_timeout_secs=settings.circuit_timeout_secs,
            price_store=paper_price_store,
            connect_timeout_secs=settings.ws_connect_timeout_secs,
            tick_publisher=tick_publisher,
        )
        self.kite_ingestor = ingestor

        symbols = [
            SymbolConfig(
                symbol=inst.symbol,
                instrument_token=inst.token,
                instrument_type=InstrumentType(inst.instrument_type),
            )
            for inst in instruments
        ]
        candle_aggregator = CandleAggregatorComponent(
            candle_aggregator=candle_registry,
            historical_data_service=historical_data_service,
            symbols=symbols,
            intervals=settings.candle_intervals,
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

        for algo in algo_configs:
            intervals = algo.candle_intervals or settings.candle_intervals
            tick_pipeline = factory.build_pipeline(
                algo=algo,
                intervals=intervals,
                instrument_type_map=instrument_type_map,
                circuit=circuit,
                candle_registry=candle_registry,
            )
            await factory.seed_state(algo, intervals)

            candle_aggregator.add_algo_registry(tick_pipeline.signal_generator)
            ingestor.add_on_tick(tick_pipeline.run)
            self.order_executor = tick_pipeline.order_executor

            logger.info(
                "ComponentContainer: algo=%r strategy=%r instruments=%d equity=%.0f",
                algo.name,
                algo.strategy_id,
                len(algo.instruments),
                algo.equity,
            )

        return Runtime([ingestor, candle_aggregator, heartbeat_monitor])


def _runtime_assembler() -> _RuntimeAssembler:
    return _RuntimeAssembler()


async def _runtime(
    assembler: _RuntimeAssembler,
    tick_registry: TickIngestor,
    candle_registry: CandleAggregator,
    historical_data_service: HistoricalDataService,
    heartbeat_monitor: HeartbeatMonitor,
    stream: BrokerStream,
    broker: Broker,
    trading: TradingStore,
    audit: AuditStore,
    chart: ChartStore,
    config_store: ConfigStore,
    price_store: AbstractPriceStore,
    settings: Settings,
    sf: async_sessionmaker[AsyncSession],
    redis: object,
    circuit: AbstractCircuitBreaker,
    cacher_factory: CacherFactory,
) -> AbstractRuntime:
    return await assembler.build_runtime(
        tick_registry=tick_registry,
        candle_registry=candle_registry,
        historical_data_service=historical_data_service,
        heartbeat_monitor=heartbeat_monitor,
        stream=stream,
        broker=broker,
        trading=trading,
        audit=audit,
        chart=chart,
        config_store=config_store,
        price_store=price_store,
        settings=settings,
        sf=sf,
        redis=redis,
        circuit=circuit,
        cacher_factory=cacher_factory,
    )


def _dashboard(
    assembler: _RuntimeAssembler,
    runtime: AbstractRuntime,  # noqa: ARG001 -- forces runtime (which sets assembler.kite_ingestor) to resolve first
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: KiteClient,
    cacher_factory: CacherFactory,
    historical_data_service: HistoricalDataService,
    clock: Clock,
) -> object | None:
    if not settings.dashboard_enabled:
        return None
    from trading.api.server import ApiServer

    return ApiServer(
        session_factory=sf,
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        clock=clock,
        candle_intervals=settings.candle_intervals,
        zerodha_api_key=settings.zerodha_api_key,
        zerodha_api_secret=settings.zerodha_api_secret,
        token_secret_key=settings.token_secret_key,
        kite_client=client,
        kite_ingestor=assembler.kite_ingestor,
        order_executor=assembler.order_executor,
        cacher_factory=cacher_factory,
        historical_data_service=historical_data_service,
        heartbeat_stale_secs=settings.heartbeat_timeout_secs,
    )


def _scheduler(
    settings: Settings,
    runtime: AbstractRuntime,
    trading: TradingStore,
    position_store: PositionStore,
    price_store: AbstractPriceStore,
    cacher_factory: CacherFactory,
    clock: Clock,
) -> Scheduler:
    on_position_reset = None
    if settings.paper_trading:
        from trading.execution.service.eod_square_off import square_off_open_positions
        from trading.execution.service.position_accountant import PositionAccountant

        accountant = PositionAccountant(position_store, cacher_factory)

        async def eod_square_off() -> None:
            await square_off_open_positions(trading, accountant, price_store, clock)

        on_position_reset = eod_square_off

    return Scheduler(
        settings,
        on_market_open=runtime.start,
        on_market_close=runtime.stop,
        on_position_reset=on_position_reset,
    )


class ComponentContainer(containers.DeclarativeContainer):
    settings = providers.Dependency(instance_of=Settings)
    clock = providers.Dependency(instance_of=Clock)
    stream = providers.Dependency(instance_of=BrokerStream)
    broker = providers.Dependency(instance_of=Broker)
    client = providers.Dependency(instance_of=KiteClient)
    sf = providers.Dependency()  # async_sessionmaker[AsyncSession]
    redis = providers.Dependency()
    candle_data_store = providers.Dependency(instance_of=CandleDataStore)
    trading = providers.Dependency(instance_of=TradingStore)
    audit = providers.Dependency(instance_of=AuditStore)
    chart = providers.Dependency(instance_of=ChartStore)
    config_store = providers.Dependency(instance_of=ConfigStore)
    price_store = providers.Dependency(instance_of=AbstractPriceStore)
    heartbeat_store = providers.Dependency(instance_of=HeartbeatStore)
    position_store = providers.Dependency(instance_of=PositionStore)
    cacher_factory = providers.Dependency(instance_of=CacherFactory)

    circuit_breaker = providers.Singleton(_circuit_breaker)

    tick_registry = providers.Resource(
        _tick_registry, stream=stream, audit=audit, sf=sf, settings=settings, circuit=circuit_breaker
    )

    candle_registry = providers.Resource(
        _candle_registry, candle=candle_data_store, audit=audit, sf=sf, settings=settings
    )

    historical_data_service = providers.Singleton(
        _historical_data_service, broker=broker, candle=candle_data_store
    )

    heartbeat_monitor = providers.Singleton(
        _heartbeat_monitor, heartbeat=heartbeat_store, sf=sf, settings=settings
    )

    _runtime_assembler_provider = providers.Singleton(_runtime_assembler)

    runtime = providers.Resource(
        _runtime,
        assembler=_runtime_assembler_provider,
        tick_registry=tick_registry,
        candle_registry=candle_registry,
        historical_data_service=historical_data_service,
        heartbeat_monitor=heartbeat_monitor,
        stream=stream,
        broker=broker,
        trading=trading,
        audit=audit,
        chart=chart,
        config_store=config_store,
        price_store=price_store,
        settings=settings,
        sf=sf,
        redis=redis,
        circuit=circuit_breaker,
        cacher_factory=cacher_factory,
    )

    dashboard = providers.Singleton(
        _dashboard,
        assembler=_runtime_assembler_provider,
        runtime=runtime,
        sf=sf,
        settings=settings,
        client=client,
        cacher_factory=cacher_factory,
        historical_data_service=historical_data_service,
        clock=clock,
    )

    scheduler = providers.Singleton(
        _scheduler,
        settings=settings,
        runtime=runtime,
        trading=trading,
        position_store=position_store,
        price_store=price_store,
        cacher_factory=cacher_factory,
        clock=clock,
    )
