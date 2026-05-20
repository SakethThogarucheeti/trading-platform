from __future__ import annotations

import logging

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.broker.base.broker import Broker
from trading.broker.base.broker_stream import BrokerStream
from trading.broker.paper_broker import AbstractPriceStore
from trading.config.settings import AlgoSettings, Settings
from trading.core.pipeline import AlgoPipeline, TickPipeline
from trading.di.providers.strategy import make_strategy
from trading.engine.candle_aggregator import CandleAggregator, CandleAggregatorComponent, CandleConfig
from trading.engine.heartbeat import HeartbeatMonitor
from trading.api.dashboard.component import DashboardServer
from trading.engine.kite_ingestor import KiteIngestor
from trading.engine.runtime import AbstractRuntime, Runtime
from trading.engine.scheduler import Scheduler
from quantindicators.polars_store import PolarsStore
from trading.strategy.signal_generator import AlgoInstance, AlgoRunConfig, SignalGenerator
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.risk.risk_filter import RiskConfig, RiskFilter
from trading.engine.tick_ingestor import TickConfig, TickIngestor
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.candle import CandleDataStore
from trading.storage.stores.chart import ChartStore
from trading.storage.stores.config import ConfigStore
from trading.storage.stores.heartbeat import HeartbeatStore
from trading.storage.stores.trading import TradingStore
from trading.core.models import Instrument

logger = logging.getLogger(__name__)


class ComponentProvider(Provider):
    scope = Scope.APP

    @provide
    async def tick_registry(
        self,
        stream: BrokerStream,
        audit: AuditStore,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
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
            circuit_timeout_secs=settings.circuit_timeout_secs,
        )

    @provide
    async def candle_registry(
        self,
        broker: Broker,
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
        return CandleAggregator(config=config, broker=broker, candle=candle, audit=audit)

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
            component_names=["heartbeat_monitor"],
            beat_interval_secs=settings.heartbeat_interval_secs,
            timeout_secs=settings.heartbeat_timeout_secs,
            alerter=_alert,
        )

    @provide
    async def runtime(
        self,
        tick_registry: TickIngestor,
        candle_registry: CandleAggregator,
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
    ) -> AbstractRuntime:
        instruments = await self._load_instruments(sf)
        instrument_type_map = {r.symbol: r.instrument_type for r in instruments}
        algo_configs = self._normalize_algo_configs(settings, instrument_type_map)

        paper_price_store = price_store if settings.paper_trading else None
        polars_store = PolarsStore()

        ingestor = KiteIngestor(
            stream=stream,
            tick_registry=tick_registry,
            price_store=paper_price_store,
            connect_timeout_secs=settings.ws_connect_timeout_secs,
        )
        candle_aggregator = CandleAggregatorComponent(candle_registry)

        for algo in algo_configs:
            intervals = algo.candle_intervals or settings.candle_intervals
            algo_reg, risk_reg, exec_reg = self._build_algo_pipeline(
                algo, intervals, instrument_type_map,
                chart, config_store, audit, trading, broker, sf,
                tick_registry, paper_price_store, polars_store, settings,
            )
            strategy = make_strategy(algo.strategy_id)
            await self._seed_algo_state(algo, settings, intervals, config_store, strategy)

            candle_aggregator.add_algo_registry(algo_reg)
            algo_pipeline = AlgoPipeline(risk_filter=risk_reg, executor=exec_reg)
            tick_pipeline = TickPipeline(
                candle_registry=candle_registry,
                signal_generator=algo_reg,
                algo_pipeline=algo_pipeline,
            )
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

    def _build_algo_pipeline(
        self,
        algo: AlgoSettings,
        intervals: list[str],
        instrument_type_map: dict[str, str],
        chart: ChartStore,
        config_store: ConfigStore,
        audit: AuditStore,
        trading: TradingStore,
        broker: Broker,
        sf: async_sessionmaker[AsyncSession],
        tick_registry: TickIngestor,
        paper_price_store: AbstractPriceStore | None,
        polars_store: PolarsStore,
        settings: Settings,
    ) -> tuple[SignalGenerator, RiskFilter, OrderExecutor]:
        from trading.core.schemas import InstrumentType

        algo_instances: dict[str, AlgoInstance] = {
            s: AlgoInstance(
                strategy=make_strategy(algo.strategy_id),
                instrument_type=InstrumentType(
                    instrument_type_map.get(s, InstrumentType.EQUITY.value)
                ),
            )
            for s in algo.instruments
        }
        algo_reg = SignalGenerator(
            config=AlgoRunConfig(
                instrument_strategy_map={s: algo.strategy_id for s in algo.instruments},
                instrument_types={
                    s: instrument_type_map.get(s, InstrumentType.EQUITY.value)
                    for s in algo.instruments
                },
                equity=algo.equity,
                warmup_candles=settings.warmup_candles,
                algo_name=algo.name,
            ),
            chart=chart,
            config_store=config_store,
            audit=audit,
            algos=algo_instances,
            store=polars_store,
        )
        risk_reg = RiskFilter(
            config=RiskConfig(
                equity=algo.equity,
                max_daily_loss_pct=settings.max_daily_loss_pct,
                risk_per_trade_pct=settings.risk_per_trade_pct,
                rc_id=algo.risk_controller_id,
                paper_trading=settings.paper_trading,
                intraday_cutoff_hour=settings.intraday_cutoff_hour,
                intraday_cutoff_minute=settings.intraday_cutoff_minute,
            ),
            circuit=tick_registry.circuit,
            trading=trading,
            audit=audit,
        )
        exec_reg = OrderExecutor(
            config=ExecConfig(exec_id=algo.execution_engine_id),
            broker=broker,
            session_factory=sf,
            trading=trading,
            price_store=paper_price_store,
        )
        return algo_reg, risk_reg, exec_reg

    async def _seed_algo_state(
        self,
        algo: AlgoSettings,
        settings: Settings,
        intervals: list[str],
        config_store: ConfigStore,
        strategy: object,
    ) -> None:
        from trading.strategy.base import Strategy

        params = strategy.get_params() if isinstance(strategy, Strategy) else {}
        await config_store.seed_algo_config(
            name=algo.name,
            strategy_id=algo.strategy_id,
            warmup_candles=settings.warmup_candles,
            candle_intervals=intervals,
            equity=algo.equity,
            params=params,
        )
        # Reset session state so bars_seen reflects only this session,
        # not stale counts from previous days.
        await config_store.upsert_algo_state(
            algo.name,
            {
                "bars_seen": 0,
                "warmup_candles": settings.warmup_candles,
                "warmup_complete": False,
                "bars_remaining": settings.warmup_candles,
                "last_signal_at": None,
            },
        )

    @provide
    def dashboard(
        self,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> DashboardServer | None:
        if not settings.dashboard_enabled:
            return None
        from trading.api.dashboard.component import DashboardServer

        return DashboardServer(
            session_factory=sf,
            host=settings.dashboard_host,
            port=settings.dashboard_port,
            candle_intervals=settings.candle_intervals,
        )

    @provide
    def scheduler(self, settings: Settings, runtime: AbstractRuntime) -> Scheduler:
        return Scheduler(
            settings,
            on_market_open=runtime.start,
            on_market_close=runtime.stop,
        )
