from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from quantindicators.polars_store import PolarsStore
from quantindicators.store import AbstractCandleStore
from quantindicators.types import CandleRow

from trading.candles.api.schemas import CandleEvent
from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractRegistry
from trading.app.tasks import fire
from trading.strategy.api.interfaces import (
    AbstractAuditStore,
    AbstractChartStore,
    AbstractConfigStore,
    CacherFactory,
)
from trading.strategy.api.schemas import SignalEvent
from trading.strategy.service.base import AlgoInstance, AlgoRunConfig, Strategy

logger = logging.getLogger(__name__)


class BarCachingStore(AbstractCandleStore):
    """Wraps any AbstractCandleStore and deduplicates fetch calls within a single bar."""

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
class SignalGeneratedContext:
    strategy_id: str
    side: str
    signal_type: str
    stop_distance: float
    algo_name: str


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
        if isinstance(store, PolarsStore):
            self._indicator_store = store
        else:
            self._indicator_store = BarCachingStore(store)  # type: ignore[assignment]

    def setup(self, warmup_candles: dict[str, list[CandleEvent]] | None = None) -> None:
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
        state_cacher = self._factory.rolling_state()
        for symbol, instance in self._algos.items():
            if not instance.interval:
                continue
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
