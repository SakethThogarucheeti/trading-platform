from __future__ import annotations

from dataclasses import dataclass

from quantindicators.polars_store import PolarsStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.app.pipeline import AlgoPipeline, TickPipeline
from trading.broker.api import Broker
from trading.candles.api import CandleAggregator
from trading.config.settings import AlgoSettings, Settings
from trading.core.messaging import AbstractCircuitBreaker
from trading.core.schemas import InstrumentType
from trading.di.providers.strategy import make_strategy
from trading.execution.api import ExecConfig, FillHandler, OrderExecutor, PositionAccountant
from trading.execution.storage.store import PositionStore, TradingStore
from trading.risk.gates.circuit_breaker import CircuitBreakerGate
from trading.risk.gates.daily_loss import DailyLossGate
from trading.risk.gates.duplicate_position import DuplicatePositionGate
from trading.risk.gates.time_cutoff import TimeCutoffGate
from trading.risk.service.filter import RiskConfig, RiskFilter
from trading.risk.service.policy import RiskGate
from trading.storage.cache import CacherFactory
from trading.strategy.api import AlgoInstance, AlgoRunConfig, SignalGenerator
from trading.strategy.storage.store import ChartStore, ConfigStore
from trading.tick_ingest.storage.store import AuditStore


@dataclass
class SharedAlgoDeps:
    chart: ChartStore
    config_store: ConfigStore
    audit: AuditStore
    trading: TradingStore
    broker: Broker
    session_factory: async_sessionmaker[AsyncSession]
    polars_store: PolarsStore
    settings: Settings
    factory: CacherFactory


class AlgoPipelineFactory:
    """
    Assembles a fully-wired TickPipeline for a single algo.

    Callers provide the per-algo inputs (algo config, intervals, instrument types,
    circuit breaker) and receive a TickPipeline ready to be registered as a tick
    callback. All shared infrastructure deps live in SharedAlgoDeps.
    """

    def __init__(self, shared: SharedAlgoDeps) -> None:
        self._s = shared

    def build_pipeline(
        self,
        algo: AlgoSettings,
        intervals: list[str],
        instrument_type_map: dict[str, str],
        circuit: AbstractCircuitBreaker,
        candle_registry: CandleAggregator,
    ) -> TickPipeline:
        s = self._s
        exec_id = "paper" if s.settings.paper_trading else algo.execution_engine_id

        algo_instances: dict[str, AlgoInstance] = {
            sym: AlgoInstance(
                strategy=make_strategy(algo.strategy_id),
                instrument_type=InstrumentType(
                    instrument_type_map.get(sym, InstrumentType.EQUITY.value)
                ),
            )
            for sym in algo.instruments
        }

        signal_generator = SignalGenerator(
            config=AlgoRunConfig(
                instrument_strategy_map={sym: algo.strategy_id for sym in algo.instruments},
                instrument_types={
                    sym: instrument_type_map.get(sym, InstrumentType.EQUITY.value)
                    for sym in algo.instruments
                },
                equity=algo.equity,
                warmup_candles=s.settings.warmup_candles,
                algo_name=algo.name,
            ),
            chart=s.chart,
            config_store=s.config_store,
            audit=s.audit,
            factory=s.factory,
            algos=algo_instances,
            store=s.polars_store,
        )

        position_store = PositionStore(s.session_factory)

        gates: list[RiskGate] = [
            TimeCutoffGate(),
            CircuitBreakerGate(circuit),
            DailyLossGate(enabled=not s.settings.paper_trading),
            DuplicatePositionGate(),
        ]

        risk_filter = RiskFilter(
            config=RiskConfig(
                equity=algo.equity,
                max_daily_loss_pct=s.settings.max_daily_loss_pct,
                risk_per_trade_pct=s.settings.risk_per_trade_pct,
                rc_id=algo.risk_controller_id,
                intraday_cutoff_hour=s.settings.intraday_cutoff_hour,
                intraday_cutoff_minute=s.settings.intraday_cutoff_minute,
            ),
            gates=gates,
            trading=s.trading,
            audit=s.audit,
            position=position_store,
            factory=s.factory,
        )

        accountant = PositionAccountant(position_store, s.factory)
        fill_handler = FillHandler(s.trading, accountant)

        order_executor = OrderExecutor(
            config=ExecConfig(exec_id=exec_id),
            broker=s.broker,
            session_factory=s.session_factory,
            trading=s.trading,
            fill_handler=fill_handler,
        )

        algo_pipeline = AlgoPipeline(risk_filter=risk_filter, executor=order_executor)
        return TickPipeline(
            candle_registry=candle_registry,
            signal_generator=signal_generator,
            algo_pipeline=algo_pipeline,
        )

    async def seed_state(self, algo: AlgoSettings, intervals: list[str]) -> None:
        s = self._s
        strategy = make_strategy(algo.strategy_id)

        params = strategy.get_params()
        await s.config_store.seed_algo_config(
            name=algo.name,
            strategy_id=algo.strategy_id,
            warmup_candles=s.settings.warmup_candles,
            candle_intervals=intervals,
            equity=algo.equity,
            params=params,
        )
        fresh = AlgoInstance(strategy=strategy, instrument_type=InstrumentType.EQUITY)
        await s.config_store.upsert_algo_state(
            algo.name, fresh.state_dict(s.settings.warmup_candles)
        )
