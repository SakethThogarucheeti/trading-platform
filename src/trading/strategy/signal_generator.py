from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field
from quantindicators.polars_store import PolarsStore
from quantindicators.store import AbstractCandleStore
from quantindicators.types import CandleRow

from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractRegistry
from trading.core.schemas import CandleEvent, InstrumentType, SignalEvent
from trading.core.tasks import fire
from trading.storage.cache import CacherFactory
from trading.storage.stores.audit import AbstractAuditStore, AuditContext
from trading.storage.stores.chart import AbstractChartStore
from trading.storage.stores.config import AbstractConfigStore
from trading.strategy.base import Strategy

logger = logging.getLogger(__name__)


class BarCachingStore(AbstractCandleStore):
    """Wraps any AbstractCandleStore and deduplicates fetch calls within a single bar.

    Call invalidate() once per bar (after pushing the new candle) so all
    indicators sharing this instance see the latest data with only one real
    fetch to the underlying store.
    """

    def __init__(self, inner: AbstractCandleStore) -> None:
        self._inner = inner
        self._cache: dict[tuple[str, ...], list[CandleRow]] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    async def fetch(self, symbol: str, interval: str, limit: int) -> list[CandleRow]:
        key = ("fetch", symbol, interval, str(limit))
        if key not in self._cache:
            self._cache[key] = await self._inner.fetch(symbol, interval, limit)
        return self._cache[key]

    async def fetch_since(self, symbol: str, interval: str, since: datetime) -> list[CandleRow]:
        key = ("fetch_since", symbol, interval, since.isoformat())
        if key not in self._cache:
            self._cache[key] = await self._inner.fetch_since(symbol, interval, since)
        return self._cache[key]


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
    interval: str = ""
    bars_seen: int = 0
    warmed_up: bool = False
    last_signal_at: str | None = None

    def tick_bar(self, interval: str, warmup_candles: int) -> None:
        self.interval = interval
        self.bars_seen += 1
        if self.bars_seen >= warmup_candles:
            self.warmed_up = True

    def record_signal(self, now: datetime) -> None:
        self.last_signal_at = now.isoformat()

    def is_ready(self) -> bool:
        return self.strategy._store is not None

    def state_dict(self, warmup_candles: int) -> dict[str, object]:
        return {
            "bars_seen": self.bars_seen,
            "warmup_candles": warmup_candles,
            "warmup_complete": self.warmed_up,
            "bars_remaining": 0 if self.warmed_up else max(0, warmup_candles - self.bars_seen),
            "last_signal_at": self.last_signal_at,
            **self.strategy.get_state(),
        }


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
        factory: CacherFactory,
        algos: dict[str, AlgoInstance] | None = None,
        store: PolarsStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._chart = chart
        self._config_store = config_store
        self._audit = audit
        self._factory = factory
        self._algos: dict[str, AlgoInstance] = algos if algos is not None else {}
        self._store: PolarsStore = store if store is not None else PolarsStore()
        self._indicator_store: AbstractCandleStore = self._store
        self._clock: Clock = clock or SystemClock()

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
            self._indicator_store = BarCachingStore(store)  # type: ignore[assignment]

    def setup(self, warmup_candles: dict[str, list[CandleEvent]] | None = None) -> None:
        """
        Initialize all strategy instances with the indicator store and pre-built indicators.

        Must be called after set_indicator_store() (if used) and before the first handle().
        Pass warmup_candles to let strategies eagerly construct per-symbol indicator instances.
        """
        candles_by_symbol = warmup_candles or {}
        for symbol, instance in self._algos.items():
            instance.strategy.set_store(self._indicator_store)
            instance.strategy.warmup(symbol, candles_by_symbol.get(symbol, []))

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

        if not instance.is_ready():
            logger.warning(
                "SignalGenerator.setup() not called for '%s' — skipping", candle.symbol
            )
            return []

        instance.tick_bar(candle.interval, self._config.warmup_candles)

        if isinstance(self._indicator_store, BarCachingStore):
            self._indicator_store.invalidate()

        instance.strategy.set_chart_callback(self._make_chart_cb(candle.symbol, candle.interval))

        signal = await instance.strategy.on_candle(candle.symbol, instance.instrument_type, candle)

        if signal is not None:
            instance.record_signal(self._clock.now())

        rolling = instance.strategy.rolling_state()
        if rolling:
            fire(
                self._factory.rolling_state().save(
                    algo=instance.strategy.id,
                    symbol=candle.symbol,
                    interval=candle.interval,
                    tick_log_id=candle.tick_log_id,
                    data=rolling,
                )
            )

        fire(self._upsert_state(instance))

        if signal is None:
            return []

        signal_event = SignalEvent.from_signal(
            signal, candle.tick_log_id, algo_name=self._config.algo_name
        )

        fire(self._log_signal(signal_event, self._config.algo_name))

        logger.info(
            "SignalGenerator[%s]: signal %s %s %s",
            self._config.algo_name,
            signal.signal_id,
            signal.side,
            signal.symbol,
        )
        return [signal_event]

    async def restore_state(self) -> None:
        """
        Restore rolling indicator state from cache for all algo instances.

        Called once at startup (after setup()) by the DI wiring layer.
        On corrupt/unusable state, clears the cache entry and falls back to DB warmup.
        """
        state_cacher = self._factory.rolling_state()
        for symbol, instance in self._algos.items():
            if not instance.interval:
                continue  # interval not yet known (no candles processed)
            result = await state_cacher.load_latest(
                algo=instance.strategy.id,
                symbol=symbol,
                interval=instance.interval,
            )
            if result is None:
                continue
            tick_log_id, state = result
            ok = await instance.strategy.restore_from_state(state)
            if ok:
                logger.info(
                    "SignalGenerator[%s]: restored state for %s from tick_log_id=%d",
                    self._config.algo_name,
                    symbol,
                    tick_log_id,
                )
            else:
                logger.warning(
                    "SignalGenerator[%s]: state invalid for %s — clearing cache, using warmup",
                    self._config.algo_name,
                    symbol,
                )
                await state_cacher.clear(
                    algo=instance.strategy.id, symbol=symbol, interval=instance.interval
                )

    async def _upsert_state(self, instance: AlgoInstance) -> None:
        try:
            await self._config_store.upsert_algo_state(
                self._config.algo_name, instance.state_dict(self._config.warmup_candles)
            )
        except Exception:
            logger.warning(
                "SignalGenerator: state upsert failed for %s", self._config.algo_name, exc_info=True
            )

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
