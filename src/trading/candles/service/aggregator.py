from __future__ import annotations

import logging
from datetime import UTC, datetime

from anyio import sleep_forever

from trading.candles.api.interfaces import AbstractCandleConsumer
from trading.candles.api.schemas import CandleEvent
from trading.candles.service.bar_accumulator import AbstractBarAccumulator, BarAccumulator, SymbolConfig
from trading.candles.service.historical import HistoricalDataService, warmup_start
from trading.candles.service.persister import AbstractCandleLogger, CandleConfig
from trading.core.clock import Clock, SystemClock
from trading.core.lifecycle.component import Component
from trading.core.messaging import AbstractRegistry
from trading.core.schemas import InstrumentType
from trading.app.tasks import fire
from trading.tick_ingest.api.schemas import TickEvent

logger = logging.getLogger(__name__)


class CandleAggregator(AbstractRegistry):
    """
    Aggregates TickEvents into OHLCV candles.

    Returns a CandleEvent when a bar closes, None while the bar is still building.
    Persistence is delegated to the injected AbstractCandleLogger so bar logic
    can be tested independently of DB state.
    """

    def __init__(
        self,
        config: CandleConfig,
        candle_logger: AbstractCandleLogger,
        accumulator: AbstractBarAccumulator | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._candle_logger = candle_logger
        self._clock: Clock = clock or SystemClock()

        self._symbols: list[SymbolConfig] = [
            SymbolConfig(
                symbol=inst.symbol,
                instrument_token=inst.token,
                instrument_type=InstrumentType(inst.instrument_type),
            )
            for inst in config.instruments
        ]
        self._token_sc: dict[int, SymbolConfig] = {sc.instrument_token: sc for sc in self._symbols}
        self._accumulator: AbstractBarAccumulator = (
            accumulator if accumulator is not None else BarAccumulator()
        )

    async def handle(self, tick: TickEvent) -> CandleEvent | None:  # type: ignore[override]
        sc = self._token_sc.get(tick.instrument_token)
        if sc is None:
            return None

        for interval in self._config.intervals:
            candle = self._accumulator.process(sc, interval, tick)
            if candle is not None:
                fire(self._candle_logger.log(candle))
                return candle

        return None


class CandleAggregatorComponent(Component):
    """
    Lifecycle component wrapping CandleAggregator.

    _setup fetches historical candles via HistoricalDataService and replays
    them through registered consumers (e.g. SignalGenerators) so strategies
    are pre-seeded before live ticks arrive.

    _run sleeps forever — live ticks are fed via KiteIngestor's on_tick callbacks.
    """

    def __init__(
        self,
        candle_aggregator: CandleAggregator,
        historical_data_service: HistoricalDataService,
        symbols: list[SymbolConfig],
        intervals: list[str],
        warmup_count: int,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(name="candle_aggregator")
        self._aggregator = candle_aggregator
        self._historical_data_service = historical_data_service
        self._symbols = symbols
        self._intervals = intervals
        self._warmup_count = warmup_count
        self._clock: Clock = clock or SystemClock()
        self._algo_callbacks: list[AbstractCandleConsumer] = []

    def add_algo_registry(self, consumer: AbstractCandleConsumer) -> None:
        """Register a candle consumer to receive warmup candles during _setup."""
        self._algo_callbacks.append(consumer)

    async def _setup(self) -> None:
        now = self._clock.now()
        start = warmup_start(now, self._intervals, self._warmup_count)

        all_candles: list[CandleEvent] = []
        for sc in self._symbols:
            for interval in self._intervals:
                try:
                    result = await self._historical_data_service.fetch(
                        sc.symbol, interval, start, now
                    )
                    df = result.df.tail(self._warmup_count)
                    for row in df.iter_rows(named=True):
                        ts: datetime = row["date"]
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=UTC)
                        all_candles.append(
                            CandleEvent(
                                symbol=sc.symbol,
                                instrument_type=sc.instrument_type,
                                interval=interval,
                                open=float(row["open"]),
                                high=float(row["high"]),
                                low=float(row["low"]),
                                close=float(row["close"]),
                                volume=int(row.get("volume", 0)),
                                timestamp=ts,
                                tick_log_id=0,
                            )
                        )
                except Exception:
                    logger.warning(
                        "CandleAggregatorComponent: warmup fetch failed for %s %s",
                        sc.symbol, interval, exc_info=True,
                    )

        all_candles.sort(key=lambda c: c.timestamp)

        candles_by_symbol: dict[str, list[CandleEvent]] = {}
        for candle in all_candles:
            candles_by_symbol.setdefault(candle.symbol, []).append(candle)

        for consumer in self._algo_callbacks:
            consumer.setup(candles_by_symbol)

        if all_candles and self._algo_callbacks:
            logger.info(
                "CandleAggregatorComponent: replaying %d warmup candles through %d consumer(s)",
                len(all_candles),
                len(self._algo_callbacks),
            )
            for candle in all_candles:
                for consumer in self._algo_callbacks:
                    try:
                        await consumer.handle(candle)
                    except Exception:
                        logger.exception(
                            "CandleAggregatorComponent: warmup replay error for %s", candle.symbol
                        )

        logger.info("CandleAggregatorComponent: warm-up complete (%d candles)", len(all_candles))

    async def _run(self) -> None:
        await sleep_forever()
