from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
from trading.di.containers.broker import BrokerDeps
from trading.di.containers.infra import Infra
from trading.di.providers.algo_pipeline import AlgoPipelineFactory, SharedAlgoDeps
from trading.execution.api import OrderExecutor
from trading.execution.service.order_reconciler import OrderReconciler
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
)
from trading.tick_ingest.storage.store import AuditStore

if TYPE_CHECKING:
    from trading.api.server import ApiServer

logger = logging.getLogger(__name__)


def _circuit_breaker() -> AbstractCircuitBreaker:
    return CircuitBreaker()


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


@dataclass
class RuntimeDeps:
    """Bundles build_runtime's dependencies -- mirrors how SharedAlgoDeps
    bundles AlgoPipelineFactory's, one layer up in di/providers/algo_pipeline.py.

    _runtime() (the dependency_injector provider function ComponentContainer
    actually wires) still takes each of these as its own named provider --
    the framework requires that -- and builds this bundle internally before
    calling build_runtime(), the same way build_runtime itself already builds
    SharedAlgoDeps before calling AlgoPipelineFactory.
    """

    tick_registry: TickIngestor
    candle_registry: CandleAggregator
    historical_data_service: HistoricalDataService
    heartbeat_monitor: HeartbeatMonitor
    stream: BrokerStream
    broker: Broker
    trading: TradingStore
    audit: AuditStore
    chart: ChartStore
    config_store: ConfigStore
    price_store: AbstractPriceStore
    settings: Settings
    sf: async_sessionmaker[AsyncSession]
    circuit: AbstractCircuitBreaker
    cacher_factory: CacherFactory


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
        self.candle_aggregator: CandleAggregatorComponent | None = None
        # Fills post back to a single shared /api/postback endpoint regardless
        # of which algo placed the order (handle_fill() is a stateless DB
        # lookup by kite_order_id), so any one algo's OrderExecutor works —
        # last one wins, consistent across single- and multi-algo configs.
        self.order_executor: OrderExecutor | None = None

    async def build_runtime(self, deps: RuntimeDeps) -> AbstractRuntime:
        instruments = await _load_instruments(deps.sf)
        instrument_type_map = {r.symbol: r.instrument_type for r in instruments}
        algo_configs = _normalize_algo_configs(deps.settings, instrument_type_map)

        paper_price_store = deps.price_store if deps.settings.paper_trading else None
        polars_store = PolarsStore()

        ingestor = KiteIngestor(
            stream=deps.stream,
            tick_registry=deps.tick_registry,
            circuit=deps.circuit,
            circuit_timeout_secs=deps.settings.circuit_timeout_secs,
            price_store=paper_price_store,
            connect_timeout_secs=deps.settings.ws_connect_timeout_secs,
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
            candle_aggregator=deps.candle_registry,
            historical_data_service=deps.historical_data_service,
            symbols=symbols,
            intervals=deps.settings.candle_intervals,
            warmup_count=deps.settings.warmup_candles,
        )
        self.candle_aggregator = candle_aggregator

        factory = AlgoPipelineFactory(SharedAlgoDeps(
            chart=deps.chart,
            config_store=deps.config_store,
            audit=deps.audit,
            trading=deps.trading,
            broker=deps.broker,
            session_factory=deps.sf,
            polars_store=polars_store,
            settings=deps.settings,
            factory=deps.cacher_factory,
        ))

        for algo in algo_configs:
            # AlgoSettings.candle_intervals is validated to at most one entry
            # (see trading.config.settings.AlgoSettings.candle_intervals_single_entry).
            # Resolving "unset" to the first global interval, rather than the
            # whole global list, is what makes every algo single-interval by
            # construction -- see trading-platform#36.
            intervals = algo.candle_intervals or [deps.settings.candle_intervals[0]]
            tick_pipeline = await factory.build_and_wire(
                algo=algo,
                intervals=intervals,
                instrument_type_map=instrument_type_map,
                circuit=deps.circuit,
                candle_registry=deps.candle_registry,
                registry_target=candle_aggregator,
            )

            ingestor.add_on_tick(tick_pipeline.run)
            self.order_executor = tick_pipeline.order_executor

            logger.info(
                "ComponentContainer: algo=%r strategy=%r instruments=%d equity=%.0f",
                algo.name,
                algo.strategy_id,
                len(algo.instruments),
                algo.equity,
            )

        return Runtime([ingestor, candle_aggregator, deps.heartbeat_monitor])


def _dashboard(
    assembler: _RuntimeAssembler,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: KiteClient,
    cacher_factory: CacherFactory,
    historical_data_service: HistoricalDataService,
    clock: Clock,
) -> ApiServer | None:
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
        candle_aggregator=assembler.candle_aggregator,
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
    kite_client: KiteClient,
    order_executor: OrderExecutor | None,
) -> Scheduler:
    on_position_reset = None
    on_order_reconcile = None
    if settings.paper_trading:
        from trading.execution.service.eod_square_off import square_off_open_positions
        from trading.execution.service.position_accountant import PositionAccountant

        accountant = PositionAccountant(position_store, trading, cacher_factory)

        async def eod_square_off() -> None:
            await square_off_open_positions(trading, accountant, price_store, clock)

        on_position_reset = eod_square_off
    elif order_executor is not None:
        # Only meaningful against Zerodha's real order book (trading-platform#31)
        # -- paper orders never reach it, PaperBroker ignores client_tag.
        # order_executor is None only when no algos are configured (no orders
        # are ever placed either), in which case there's nothing to reconcile.
        reconciler = OrderReconciler(trading, kite_client, order_executor)
        on_order_reconcile = reconciler.reconcile_once

    return Scheduler(
        settings,
        on_market_open=runtime.start,
        on_market_close=runtime.stop,
        on_position_reset=on_position_reset,
        on_order_reconcile=on_order_reconcile,
    )


@dataclass
class Components:
    circuit_breaker: AbstractCircuitBreaker
    tick_registry: TickIngestor
    candle_registry: CandleAggregator
    historical_data_service: HistoricalDataService
    heartbeat_monitor: HeartbeatMonitor
    runtime: AbstractRuntime
    dashboard: ApiServer | None
    scheduler: Scheduler


async def build_components(infra: Infra, broker: BrokerDeps) -> Components:
    """
    Build the per-process runtime components: the tick/candle registries, the
    per-algo pipelines and CandleAggregatorComponent wiring (via
    _RuntimeAssembler), the dashboard API server, and the scheduler.

    Replaces the old ComponentContainer (trading-platform#3) -- each provider
    function above is called directly in dependency order instead of through
    a dependency_injector provider graph.
    """
    circuit_breaker = _circuit_breaker()

    tick_registry = await _tick_registry(
        stream=broker.broker_stream,
        audit=infra.audit_store,
        sf=infra.session_factory,
        settings=infra.settings,
        circuit=circuit_breaker,
    )

    candle_registry = await _candle_registry(
        candle=infra.candle_data_store,
        audit=infra.audit_store,
        sf=infra.session_factory,
        settings=infra.settings,
    )

    historical_data_service = _historical_data_service(
        broker=broker.broker, candle=infra.candle_data_store
    )

    heartbeat_monitor = _heartbeat_monitor(
        heartbeat=infra.heartbeat_store, sf=infra.session_factory, settings=infra.settings
    )

    assembler = _RuntimeAssembler()
    runtime_deps = RuntimeDeps(
        tick_registry=tick_registry,
        candle_registry=candle_registry,
        historical_data_service=historical_data_service,
        heartbeat_monitor=heartbeat_monitor,
        stream=broker.broker_stream,
        broker=broker.broker,
        trading=infra.trading_store,
        audit=infra.audit_store,
        chart=infra.chart_store,
        config_store=infra.config_store,
        price_store=infra.price_store,
        settings=infra.settings,
        sf=infra.session_factory,
        circuit=circuit_breaker,
        cacher_factory=infra.cacher_factory,
    )
    runtime = await assembler.build_runtime(runtime_deps)

    dashboard = _dashboard(
        assembler=assembler,
        sf=infra.session_factory,
        settings=infra.settings,
        client=broker.kite_client,
        cacher_factory=infra.cacher_factory,
        historical_data_service=historical_data_service,
        clock=infra.clock,
    )

    scheduler = _scheduler(
        settings=infra.settings,
        runtime=runtime,
        trading=infra.trading_store,
        position_store=infra.position_store,
        price_store=infra.price_store,
        cacher_factory=infra.cacher_factory,
        clock=infra.clock,
        kite_client=broker.kite_client,
        order_executor=assembler.order_executor,
    )

    return Components(
        circuit_breaker=circuit_breaker,
        tick_registry=tick_registry,
        candle_registry=candle_registry,
        historical_data_service=historical_data_service,
        heartbeat_monitor=heartbeat_monitor,
        runtime=runtime,
        dashboard=dashboard,
        scheduler=scheduler,
    )
