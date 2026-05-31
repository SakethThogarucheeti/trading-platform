from __future__ import annotations

import logging

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from quantindicators.polars_store import PolarsStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.api.server import ApiServer
from trading.broker.base.broker import Broker
from trading.broker.base.broker_stream import BrokerStream
from trading.broker.paper_broker import AbstractPriceStore
from trading.broker.zerodha.kite_client import KiteClient
from trading.candles.candle_aggregator import (
    CandleAggregator,
    CandleAggregatorComponent,
    CandleConfig,
    CandlePersister,
)
from trading.candles.historical_data_service import HistoricalDataService
from trading.config.settings import AlgoSettings, Settings
from trading.core.lifecycle.runtime import AbstractRuntime, Runtime
from trading.core.messaging import AbstractCircuitBreaker
from trading.core.models import Instrument
from trading.di.providers.algo_pipeline import AlgoPipelineFactory, SharedAlgoDeps
from trading.monitoring.heartbeat import HeartbeatMonitor
from trading.monitoring.scheduler import Scheduler
from trading.core.clock import Clock
from trading.storage.cache import CacherFactory
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.candle import CandleDataStore
from trading.storage.stores.chart import ChartStore
from trading.storage.stores.config import ConfigStore
from trading.storage.stores.heartbeat import HeartbeatStore
from trading.storage.stores.position import PositionStore
from trading.storage.stores.trading import TradingStore
from trading.tick_ingest.kite_ingestor import KiteIngestor
from trading.tick_ingest.tick_ingestor import CircuitBreaker, TickConfig, TickIngestor
from trading.tick_ingest.tick_publisher import TickPublisher

logger = logging.getLogger(__name__)


class ComponentProvider(Provider):
    scope = Scope.APP

    def __init__(self) -> None:
        super().__init__()
        self._kite_ingestor: KiteIngestor | None = None

    @provide
    def circuit_breaker(self) -> AbstractCircuitBreaker:
        return CircuitBreaker()

    @provide
    async def tick_registry(
        self,
        stream: BrokerStream,
        audit: AuditStore,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
        circuit: AbstractCircuitBreaker,
    ) -> TickIngestor:
        from sqlalchemy import select

        async with sf() as session:
            instruments = list((await session.execute(select(Instrument))).scalars().all())

        exec_id = "paper" if settings.paper_trading else "direct"
        config = TickConfig(instruments=instruments, exec_id=exec_id)
        return TickIngestor(
            config=config,
            stream=stream,
            audit=audit,
            circuit=circuit,
        )

    @provide
    async def candle_registry(
        self,
        candle: CandleDataStore,
        audit: AuditStore,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> CandleAggregator:
        from sqlalchemy import select

        async with sf() as session:
            instruments = list((await session.execute(select(Instrument))).scalars().all())

        config = CandleConfig(
            instruments=instruments,
            intervals=settings.candle_intervals,
            warmup_count=settings.warmup_candles,
        )
        return CandleAggregator(config=config, candle_logger=CandlePersister(candle, audit))

    @provide
    def historical_data_service(
        self,
        broker: Broker,
        candle: CandleDataStore,
    ) -> HistoricalDataService:
        return HistoricalDataService(broker=broker, candle_store=candle)

    @provide
    def heartbeat_monitor(
        self,
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

    @provide
    async def runtime(
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
        instruments = await self._load_instruments(sf)
        instrument_type_map = {r.symbol: r.instrument_type for r in instruments}
        algo_configs = self._normalize_algo_configs(settings, instrument_type_map)

        paper_price_store = price_store if settings.paper_trading else None
        polars_store = PolarsStore()

        tick_publisher = TickPublisher(redis) if redis is not None else None

        ingestor = KiteIngestor(
            stream=stream,
            tick_registry=tick_registry,
            circuit=circuit,
            circuit_timeout_secs=settings.circuit_timeout_secs,
            price_store=paper_price_store,
            connect_timeout_secs=settings.ws_connect_timeout_secs,
            tick_publisher=tick_publisher,
        )
        self._kite_ingestor = ingestor

        from trading.candles.bar_accumulator import SymbolConfig
        from trading.core.schemas import InstrumentType

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

            logger.info(
                "ComponentProvider: algo=%r strategy=%r risk=%r exec=%r instruments=%d equity=%.0f",
                algo.name,
                algo.strategy_id,
                algo.risk_controller_id,
                algo.execution_engine_id,
                len(algo.instruments),
                algo.equity,
            )

        return Runtime([ingestor, candle_aggregator, heartbeat_monitor])

    async def _load_instruments(
        self, sf: async_sessionmaker[AsyncSession]
    ) -> list[Instrument]:
        from sqlalchemy import select

        async with sf() as session:
            return list((await session.execute(select(Instrument))).scalars().all())

    def _normalize_algo_configs(
        self, settings: Settings, instrument_type_map: dict[str, str]
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

    @provide
    def dashboard(
        self,
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
            kite_ingestor=self._kite_ingestor,
            cacher_factory=cacher_factory,
            historical_data_service=historical_data_service,
            heartbeat_stale_secs=settings.heartbeat_timeout_secs,
        )

    @provide
    def scheduler(
        self,
        settings: Settings,
        runtime: AbstractRuntime,
        trading: TradingStore,
        position_store: PositionStore,
        price_store: AbstractPriceStore,
        clock: Clock,
    ) -> Scheduler:
        on_position_reset = None
        if settings.paper_trading:
            from sqlalchemy import select

            from trading.core.models import Position
            from trading.core.schemas import FillEvent, Side

            async def eod_square_off() -> None:
                sf: async_sessionmaker[AsyncSession] = trading._sf  # type: ignore[attr-defined]
                async with sf() as session:
                    result = await session.execute(select(Position).where(Position.net_qty != 0))
                    open_positions = result.scalars().all()

                if not open_positions:
                    return

                for pos in open_positions:
                    raw_price = price_store.get(pos.symbol)
                    last_price = float(raw_price) if raw_price is not None else float(pos.avg_price)
                    side = Side.SELL if pos.net_qty > 0 else Side.BUY
                    qty = abs(pos.net_qty)
                    fill = FillEvent(
                        kite_order_id=f"EOD_{pos.symbol}_{clock.today().isoformat()}",
                        avg_price=last_price,
                        filled_qty=qty,
                        timestamp=clock.now(),
                    )
                    await position_store.update_position(
                        fill, side, pos.symbol, pos.instrument_type
                    )
                    logger.info(
                        "EOD square-off: %s %s x%d @ %.2f",
                        side.value,
                        pos.symbol,
                        qty,
                        last_price,
                    )

            on_position_reset = eod_square_off

        return Scheduler(
            settings,
            on_market_open=runtime.start,
            on_market_close=runtime.stop,
            on_position_reset=on_position_reset,
        )
