from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from testing.backtesting.data_loader import DataLoader
from testing.backtesting.metrics import (
    cagr,
    calmar_ratio,
    max_drawdown,
    max_drawdown_duration,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from testing.backtesting.portfolio import EquityTracker
from testing.backtesting.report import BacktestConfig, BacktestReport
from testing.registry import session_type
from testing.session import TestingSession
from testing.simulators.execution_sim import SlippageFillSimulator
from trading.broker.paper_broker import PriceStore
from trading.core.clock import SimulatedClock
from trading.core.database import build_session_factory, init_db
from trading.core.schemas import CandleEvent, InstrumentType
from trading.di.providers.strategy import make_strategy
from quantindicators.polars_store import PolarsStore
from trading.strategy.signal_generator import AlgoInstance, AlgoRunConfig, SignalGenerator
from trading.candles.bar_accumulator import SymbolConfig
from trading.execution.fill_handler import FillHandler
from trading.execution.order_executor import ExecConfig, OrderExecutor
from trading.execution.position_accountant import PositionAccountant
from trading.risk.gates.circuit_breaker import CircuitBreakerGate
from trading.risk.gates.daily_loss import DailyLossGate
from trading.risk.gates.duplicate_position import DuplicatePositionGate
from trading.risk.gates.time_cutoff import TimeCutoffGate
from trading.risk.risk_filter import RiskConfig, RiskFilter
from trading.tick_ingest.tick_ingestor import CircuitBreaker
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.chart import ChartStore
from trading.storage.stores.config import ConfigStore
from trading.storage.stores.position import PositionStore
from trading.storage.stores.trading import TradingStore

logger = logging.getLogger(__name__)


@session_type("backtest")
class BacktestSession(TestingSession):
    """
    Full backtest session.

    Drives the live AlgoRegistry → RiskRegistry → ExecRegistry pipeline
    with historical candle data replayed via CandlePlayer. The only replaced
    component is the Broker: ``SlippageFillSimulator`` replaces ``ZerodhaBroker``.

    Components run in the same order as live trading, giving identical
    signal/risk/execution logic — the only difference is the data source.
    """

    _config_cls = BacktestConfig

    def __init__(
        self,
        config: BacktestConfig,
        db_engine: AsyncEngine,
        results_dir: Path,
        db_schema: str = "public",
        keep_schema: bool = False,
    ) -> None:
        super().__init__(results_dir=results_dir)
        self._config = config
        self._db_engine = db_engine
        self._db_schema = db_schema
        self._keep_schema = keep_schema

    async def run(self) -> BacktestReport:
        config = self._config
        session_id = config.session_id or str(uuid.uuid4())
        config.session_id = session_id
        started_at = self._now()

        logger.info(
            "BacktestSession[%s]: starting  algo=%s  start=%s  end=%s  equity=%.0f",
            self._db_schema,
            config.algo.name,
            config.start.date(),
            config.end.date(),
            config.initial_equity,
        )

        partial_report: BacktestReport | None = None
        schema_engine = None
        tracker = EquityTracker(config.initial_equity)

        try:
            # ------------------------------------------------------------------
            # 1. Infrastructure — schema-isolated engine for parallel safety
            # ------------------------------------------------------------------
            schema_engine = await _make_schema_engine(self._db_engine, self._db_schema)
            sf = build_session_factory(schema_engine)
            await init_db(schema_engine)
            logger.debug("BacktestSession[%s]: DB schema ready", self._db_schema)

            # ------------------------------------------------------------------
            # 2. Simulator broker + price store
            # ------------------------------------------------------------------
            price_store = PriceStore()
            simulator = SlippageFillSimulator(
                price_store=price_store,
                slippage_pct=config.slippage_pct,
                partial_fill_prob=config.partial_fill_prob,
                latency_secs=config.latency_secs,
            )

            # ------------------------------------------------------------------
            # 3. Load OHLCV data (raises FileNotFoundError / ValueError early)
            # ------------------------------------------------------------------
            algo = config.algo
            intervals = algo.candle_intervals or ["1min", "5min", "15min"]
            symbol_configs = [
                SymbolConfig(
                    symbol=s,
                    instrument_token=0,
                    instrument_type=InstrumentType.EQUITY,
                )
                for s in algo.instruments
            ]
            data = _load_data(config.loader, algo.instruments, intervals, config.start, config.end)

            # Pre-populate price store with first known prices
            for (sym, _), df in data.items():
                if len(df) > 0:
                    price_store.update(sym, float(df["close"][0]))

            # ------------------------------------------------------------------
            # 4. Simulated clock
            #    Advanced at each candle bar so the risk registry sees the
            #    bar's timestamp for the intraday cutoff check.
            # ------------------------------------------------------------------
            sim_clock = SimulatedClock()

            # ------------------------------------------------------------------
            # 5. Build the direct pipeline: AlgoRegistry → RiskRegistry → ExecRegistry
            # ------------------------------------------------------------------
            algo_instances: dict[str, AlgoInstance] = {
                s: AlgoInstance(
                    strategy=make_strategy(
                        algo.strategy_id, config.strategy_params or None, clock=sim_clock
                    ),
                    instrument_type=InstrumentType.EQUITY,
                )
                for s in algo.instruments
            }

            polars_store = PolarsStore()

            setup_cache(None)
            factory = CacherFactory(ValueCache(), sim_clock)

            audit = AuditStore(sf)
            trading = TradingStore(sf)
            position = PositionStore(sf)
            accountant = PositionAccountant(position, factory)
            fill_handler = FillHandler(trading, accountant)

            algo_reg = SignalGenerator(
                config=AlgoRunConfig(
                    instrument_strategy_map={s: algo.strategy_id for s in algo.instruments},
                    equity=config.initial_equity,
                    warmup_candles=200,
                    algo_name=algo.name,
                    instrument_types={s: InstrumentType.EQUITY.value for s in algo.instruments},
                ),
                chart=ChartStore(sf),
                config_store=ConfigStore(sf),
                audit=audit,
                factory=factory,
                algos=algo_instances,
                store=polars_store,
                clock=sim_clock,
            )
            algo_reg.setup()

            risk_reg = RiskFilter(
                config=RiskConfig(
                    equity=config.initial_equity,
                    intraday_cutoff_hour=23,
                    intraday_cutoff_minute=59,
                ),
                gates=[
                    TimeCutoffGate(),
                    CircuitBreakerGate(CircuitBreaker()),
                    DailyLossGate(enabled=False),
                    DuplicatePositionGate(),
                ],
                trading=trading,
                audit=audit,
                position=position,
                factory=factory,
                clock=sim_clock,
                equity_provider=lambda: tracker.current_equity,
            )

            exec_reg = OrderExecutor(
                config=ExecConfig(exec_id="paper"),
                broker=simulator,
                session_factory=sf,
                trading=trading,
                fill_handler=fill_handler,
                clock=sim_clock,
            )

            # ------------------------------------------------------------------
            # 6. on_candle drives the full pipeline for each bar
            # ------------------------------------------------------------------
            async def _on_candle(candle: CandleEvent) -> None:
                signals = await algo_reg.handle(candle)
                for signal in signals:
                    order_event = await risk_reg.handle(signal)
                    if order_event is not None:
                        price_before = price_store.get(candle.symbol) or candle.close
                        await exec_reg.handle(order_event)
                        # SlippageFillSimulator updates price_store with fill price
                        # during place_order; read it back for equity tracking.
                        fill_price = price_store.get(candle.symbol) or price_before
                        tracker.process_fill(
                            symbol=candle.symbol,
                            side=signal.side,
                            qty=order_event.quantity,
                            price=fill_price,
                            ts=candle.timestamp,
                        )
                # Per-bar mark-to-market snapshot so the equity curve captures
                # unrealised P&L on open positions between fills.
                current_prices = {s: price_store.get(s) or candle.close for s in algo.instruments}
                tracker.mark_snapshot(candle.timestamp, current_prices)

            # ------------------------------------------------------------------
            # 7. CandlePlayer
            # ------------------------------------------------------------------
            bars_done: list[int] = [0]

            async def _on_progress(n: int, bar_ts: datetime) -> None:
                bars_done[0] = n
                sim_clock.advance(bar_ts)

            from testing.simulators.candle_player import CandlePlayer
            from trading.core.lifecycle.runtime import Runtime

            runtime = Runtime([])  # no components — pipeline is driven inline

            candle_player = CandlePlayer(
                symbols=symbol_configs,
                intervals=intervals,
                start=config.start,
                end=config.end,
                runtime=runtime,
                on_candle=_on_candle,
                on_progress=_on_progress,
                data=data,
                replay_delay_secs=config.replay_delay_secs,
                on_bar_price=price_store.update,
            )

            # ------------------------------------------------------------------
            # 8. Run — CandlePlayer calls runtime.stop() when replay is done
            # ------------------------------------------------------------------
            import anyio

            async with anyio.create_task_group() as tg:
                tg.start_soon(candle_player.start)
                tg.start_soon(runtime.start)

            # ------------------------------------------------------------------
            # 9. Close open positions + compute metrics
            # ------------------------------------------------------------------
            last_prices: dict[str, float] = {s: price_store.get(s) or 0.0 for s in algo.instruments}
            tracker.close_open_positions(last_prices)

            eq_curve = tracker.equity_curve
            trades = tracker.trades

            report = BacktestReport(
                config=config,
                equity_curve=eq_curve,
                trades=trades,
                sharpe_ratio=sharpe_ratio(eq_curve),
                max_drawdown=max_drawdown(eq_curve),
                max_drawdown_duration=max_drawdown_duration(eq_curve),
                win_rate=win_rate(trades),
                profit_factor=profit_factor(trades),
                cagr=cagr(eq_curve, config.initial_equity, start=config.start, end=config.end),
                calmar_ratio=calmar_ratio(eq_curve, start=config.start, end=config.end),
                total_trades=len(trades),
                final_equity=tracker.current_equity,
                session_id=session_id,
                session_type="backtest",
                started_at=started_at,
                finished_at=self._now(),
            )
            partial_report = report
            logger.info(
                "BacktestSession[%s]: complete  trades=%d  sharpe=%.3f  pnl=%+.0f"
                "  win_rate=%.0f%%  max_dd=%.1f%%  equity=%.0f",
                self._db_schema,
                report.total_trades,
                report.sharpe_ratio,
                report.final_equity - config.initial_equity,
                report.win_rate * 100,
                report.max_drawdown * 100,
                report.final_equity,
            )
            return report

        finally:
            if partial_report is not None:
                await self._persist(partial_report)
            if schema_engine is not None:
                if not self._keep_schema:
                    await _drop_schema(self._db_engine, self._db_schema)
                await schema_engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_data(
    loader: DataLoader,
    symbols: list[str],
    intervals: list[str],
    start: datetime,
    end: datetime,
) -> dict[tuple[str, str], object]:
    """Load all symbol × interval combinations upfront."""
    import polars as pl

    data: dict[tuple[str, str], pl.DataFrame] = {}
    for symbol in symbols:
        for interval in intervals:
            try:
                df = loader.load(symbol, interval, start, end)
                data[(symbol, interval)] = df
            except FileNotFoundError:
                logger.warning("BacktestSession: no data for %s/%s — skipping", symbol, interval)
    return data


# Intra-process lock: serializes DDL coroutines within the same process.
_schema_create_lock = asyncio.Lock()


async def _make_schema_engine(base_engine: AsyncEngine, schema: str) -> AsyncEngine:
    """
    Create (or reset) a Postgres schema and return an engine whose connections
    have ``search_path`` pinned to that schema.

    DDL is serialized via a process-level asyncio.Lock. Run grid searches
    sequentially (one pytest process at a time) to avoid cross-process catalog
    deadlocks — Postgres cannot serialize DDL across separate OS processes.
    """
    async with _schema_create_lock:
        async with base_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    return create_async_engine(
        base_engine.url,
        echo=False,
        connect_args={
            "server_settings": {"search_path": schema},
            "prepared_statement_cache_size": 0,
        },
    )


async def _drop_schema(base_engine: AsyncEngine, schema: str) -> None:
    """Drop the isolated schema after the session completes."""
    if schema == "public":
        return  # never drop the default schema

    async with _schema_create_lock:
        async with base_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
