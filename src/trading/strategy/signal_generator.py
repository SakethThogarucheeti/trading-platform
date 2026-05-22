from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from trading.core.messaging import AbstractRegistry
from trading.core.schemas import CandleEvent, InstrumentType, SignalEvent
from trading.core.tasks import fire
from quantindicators.polars_store import PolarsStore
from quantindicators.store import AbstractCandleStore, BarCachingStore
from trading.storage.stores.audit import AbstractAuditStore, AuditContext
from trading.storage.stores.chart import AbstractChartStore
from trading.storage.stores.config import AbstractConfigStore
from trading.strategy.base import Strategy

logger = logging.getLogger(__name__)


@dataclass
class SignalGeneratedContext(AuditContext):
    strategy_id: str
    side: str
    signal_type: str
    stop_distance: float
    algo_name: str


class AlgoRunConfig(BaseModel):
    instrument_strategy_map: dict[str, str]
    equity: float = Field(default=100_000.0, gt=0)
    warmup_candles: int = Field(default=200, gt=0)
    algo_name: str = "default"
    instrument_types: dict[str, str] = Field(default_factory=dict)
    session_id: str | None = None


@dataclass
class AlgoInstance:
    strategy: Strategy
    instrument_type: InstrumentType
    bars_seen: int = 0
    warmed_up: bool = False
    last_signal_at: str | None = None


class SignalGenerator(AbstractRegistry):
    """
    Runs one strategy instance per instrument.

    Each CandleEvent is pushed into the shared PolarsStore so indicator
    objects can fetch() the latest bars, then strategy.on_candle() is called.
    """

    def __init__(
        self,
        config: AlgoRunConfig,
        chart: AbstractChartStore,
        config_store: AbstractConfigStore,
        audit: AbstractAuditStore,
        algos: dict[str, AlgoInstance] | None = None,
        store: PolarsStore | None = None,
    ) -> None:
        self._config = config
        self._chart = chart
        self._config_store = config_store
        self._audit = audit
        self._algos: dict[str, AlgoInstance] = algos if algos is not None else {}
        self._store: PolarsStore = store if store is not None else PolarsStore()
        self._indicator_store: AbstractCandleStore = self._store

        if not self._algos:
            logger.warning(
                "SignalGenerator[%s]: no algo instances — will produce no signals",
                config.algo_name,
            )

    @property
    def config(self) -> AlgoRunConfig:
        return self._config

    @property
    def algos(self) -> dict[str, AlgoInstance]:
        return self._algos

    def set_indicator_store(self, store: AbstractCandleStore) -> None:
        # PolarsStore is already in-memory; only wrap external stores to deduplicate DB calls.
        if isinstance(store, PolarsStore):
            self._indicator_store = store
        else:
            self._indicator_store = BarCachingStore(store)

    def _make_chart_cb(
        self, symbol: str, interval: str
    ) -> Callable[[str, str, float, datetime], None]:
        def _cb(chart: str, series: str, value: float, ts: datetime) -> None:
            fire(self._log_chart(chart, series, value, ts, symbol, interval))

        return _cb

    async def _log_chart(
        self, chart: str, series: str, value: float, ts: datetime, symbol: str, interval: str
    ) -> None:
        try:
            await self._chart.log_indicator(
                algo_name=self._config.algo_name,
                symbol=symbol,
                interval=interval,
                chart=chart,
                series=series,
                ts=ts,
                value=value,
                session_id=self._config.session_id,
            )
        except Exception:
            logger.warning("SignalGenerator: indicator log failed for %s/%s", chart, series)

    async def handle(self, candle: CandleEvent) -> list[SignalEvent]:  # type: ignore[override]
        instance = self._algos.get(candle.symbol)
        if instance is None:
            return []

        # Push candle into the in-memory store so indicators can fetch it
        self._store.push(
            candle.symbol,
            candle.interval,
            {
                "symbol": candle.symbol,
                "interval": candle.interval,
                "ts": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            },
        )

        if instance.bars_seen == 0:
            instance.strategy.set_store(self._indicator_store)

        instance.bars_seen += 1

        if isinstance(self._indicator_store, BarCachingStore):
            self._indicator_store.invalidate()

        instance.strategy.set_chart_callback(self._make_chart_cb(candle.symbol, candle.interval))

        signal = await instance.strategy.on_candle(candle.symbol, instance.instrument_type, candle)

        if instance.bars_seen >= self._config.warmup_candles:
            instance.warmed_up = True

        if signal is not None:
            instance.last_signal_at = datetime.now(UTC).isoformat()

        fire(self._upsert_state(instance))

        if signal is None:
            return []

        signal_event = SignalEvent.from_signal(signal, candle.tick_log_id, algo_name=self._config.algo_name)

        fire(self._log_signal(signal_event, self._config.algo_name))

        logger.info(
            "SignalGenerator[%s]: signal %s %s %s",
            self._config.algo_name,
            signal.signal_id,
            signal.side,
            signal.symbol,
        )
        return [signal_event]

    async def _upsert_state(self, instance: AlgoInstance) -> None:
        warmup_complete = instance.warmed_up
        state: dict[str, object] = {
            "bars_seen": instance.bars_seen,
            "warmup_candles": self._config.warmup_candles,
            "warmup_complete": warmup_complete,
            "bars_remaining": (
                0 if warmup_complete else max(0, self._config.warmup_candles - instance.bars_seen)
            ),
            "last_signal_at": instance.last_signal_at,
            **instance.strategy.get_state(),
        }
        try:
            await self._config_store.upsert_algo_state(self._config.algo_name, state)
        except Exception:
            logger.warning("SignalGenerator: state upsert failed for %s", self._config.algo_name, exc_info=True)

    async def _log_signal(self, event: SignalEvent, algo_name: str) -> None:
        if event.tick_log_id <= 0:
            return
        try:
            await self._audit.log_decision(
                step="SIGNAL_GENERATED",
                symbol=event.symbol,
                tick_log_id=event.tick_log_id,
                context=SignalGeneratedContext(
                    strategy_id=event.strategy_id,
                    side=event.side.value,
                    signal_type=event.signal_type.value,
                    stop_distance=event.stop_distance,
                    algo_name=algo_name,
                ),
                algo_name=algo_name,
                signal_id=event.signal_id,
            )
        except Exception:
            logger.exception("SignalGenerator: decision log failed for signal %s", event.signal_id)
