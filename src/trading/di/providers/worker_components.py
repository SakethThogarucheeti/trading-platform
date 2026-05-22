from __future__ import annotations

import logging

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.broker.base.broker import Broker
from trading.broker.paper_broker import AbstractPriceStore
from trading.config.settings import AlgoSettings, Settings
from trading.core.models import Instrument
from trading.core.pipeline import AlgoPipeline, TickPipeline
from trading.di.providers.strategy import make_strategy
from trading.candles.candle_aggregator import CandleAggregator, CandleAggregatorComponent, CandleConfig
from trading.worker.circuit_breaker_redis import RedisCircuitBreaker
from trading.monitoring.heartbeat import HeartbeatMonitor
from trading.core.lifecycle.runtime import AbstractRuntime, Runtime
from trading.monitoring.scheduler import Scheduler
from trading.tick_ingest.tick_ingestor import TickConfig
from trading.worker.tick_subscriber import TickSubscriber
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.risk.risk_filter import RiskConfig, RiskFilter
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.candle import CandleDataStore
from trading.storage.stores.chart import ChartStore
from trading.storage.stores.config import ConfigStore
from trading.storage.stores.heartbeat import HeartbeatStore
from trading.storage.stores.trading import TradingStore
from trading.strategy.signal_generator import AlgoInstance, AlgoRunConfig, SignalGenerator
from quantindicators.polars_store import PolarsStore

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
            broker=broker,
            candle=candle_data_store,
            audit=audit,
        )
        candle_aggregator_component = CandleAggregatorComponent(candle_aggregator)

        # Resolve instrument tokens for subscription
        algo_symbols = set(algo.instruments)
        tokens = [r.token for r in instruments if r.symbol in algo_symbols]
        token_symbol_for_algo = {t: s for t, s in token_symbol.items() if s in algo_symbols}

        tick_subscriber = TickSubscriber(
            redis=redis,
            tokens=tokens,
            circuit_breaker=circuit_breaker,
            token_symbol=token_symbol_for_algo,
            price_store=paper_price_store,
        )

        # Build algo pipeline (same logic as ComponentProvider._build_algo_pipeline)
        exec_id = "paper" if settings.paper_trading else algo.execution_engine_id
        algo_instances = {
            s: AlgoInstance(
                strategy=make_strategy(algo.strategy_id),
                instrument_type=self._instrument_type(s, instrument_type_map),
            )
            for s in algo.instruments
        }
        signal_generator = SignalGenerator(
            config=AlgoRunConfig(
                instrument_strategy_map={s: algo.strategy_id for s in algo.instruments},
                instrument_types={s: instrument_type_map.get(s, "EQUITY") for s in algo.instruments},
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
        risk_filter = RiskFilter(
            config=RiskConfig(
                equity=algo.equity,
                max_daily_loss_pct=settings.max_daily_loss_pct,
                risk_per_trade_pct=settings.risk_per_trade_pct,
                rc_id=algo.risk_controller_id,
                paper_trading=settings.paper_trading,
                intraday_cutoff_hour=settings.intraday_cutoff_hour,
                intraday_cutoff_minute=settings.intraday_cutoff_minute,
            ),
            circuit=circuit_breaker,
            trading=trading,
            audit=audit,
        )
        order_executor = OrderExecutor(
            config=ExecConfig(exec_id=exec_id),
            broker=broker,
            session_factory=sf,
            trading=trading,
            price_store=paper_price_store,
        )

        strategy = make_strategy(algo.strategy_id)
        await config_store.seed_algo_config(
            name=algo.name,
            strategy_id=algo.strategy_id,
            warmup_candles=settings.warmup_candles,
            candle_intervals=intervals,
            equity=algo.equity,
            params=strategy.get_params() if hasattr(strategy, "get_params") else {},
        )
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

        candle_aggregator_component.add_algo_registry(signal_generator)
        algo_pipeline = AlgoPipeline(risk_filter=risk_filter, executor=order_executor)
        tick_pipeline = TickPipeline(
            candle_registry=candle_aggregator,
            signal_generator=signal_generator,
            algo_pipeline=algo_pipeline,
        )
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

    def _instrument_type(self, symbol: str, instrument_type_map: dict[str, str]) -> object:
        from trading.core.schemas import InstrumentType

        return InstrumentType(instrument_type_map.get(symbol, InstrumentType.EQUITY.value))

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
