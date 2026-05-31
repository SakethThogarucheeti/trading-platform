from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from anyio import sleep_forever
from pydantic import BaseModel, Field

from trading.candles.bar_accumulator import AbstractBarAccumulator, BarAccumulator, SymbolConfig
from trading.candles.historical_data_service import HistoricalDataService, warmup_start
from trading.core.clock import Clock, SystemClock
from trading.core.lifecycle.component import Component
from trading.core.messaging import AbstractRegistry
from trading.core.models import Instrument
from trading.core.schemas import CandleEvent, InstrumentType, TickEvent
from trading.core.tasks import fire
from trading.storage.stores.audit import AbstractAuditStore, AuditContext
from trading.storage.stores.candle import AbstractCandleDataStore
from trading.strategy.signal_generator import SignalGenerator

logger = logging.getLogger(__name__)


@dataclass
class CandleEmittedContext(AuditContext):
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    candle_ts: str


class CandleConfig(BaseModel):
    """Configuration for the candle aggregation stage."""

    model_config = {"arbitrary_types_allowed": True}

    instruments: list[Instrument]
    intervals: list[str]
    warmup_count: int = Field(default=200, gt=0)


class AbstractCandleLogger(Protocol):
    """Persist a closed candle and write the audit decision log entry."""

    async def log(self, event: CandleEvent) -> None: ...


class CandlePersister:
    """
    Concrete implementation of AbstractCandleLogger.

    Saves the candle row to the DB and, when tick_log_id is non-zero,
    writes a CANDLE_EMITTED entry to the audit decision log.
    """

    def __init__(self, candle: AbstractCandleDataStore, audit: AbstractAuditStore) -> None:
        self._candle = candle
        self._audit = audit

    async def log(self, event: CandleEvent) -> None:
        try:
            await self._candle.save_candles(
                [
                    {
                        "symbol": event.symbol,
                        "interval": event.interval,
                        "ts": event.timestamp,
                        "open": event.open,
                        "high": event.high,
                        "low": event.low,
                        "close": event.close,
                        "volume": event.volume,
                    }
                ]
            )
            if event.tick_log_id > 0:
                await self._audit.log_decision(
                    step="CANDLE_EMITTED",
                    symbol=event.symbol,
                    tick_log_id=event.tick_log_id,
                    context=CandleEmittedContext(
                        interval=event.interval,
                        open=event.open,
                        high=event.high,
                        low=event.low,
                        close=event.close,
                        volume=event.volume,
                        candle_ts=event.timestamp.isoformat(),
                    ),
                )
        except Exception as exc:
            logger.error(
                "CandlePersister: candle persist/log failed for %s %s — %s: %s",
                event.symbol,
                event.interval,
                type(exc).__name__,
                exc,
            )


class CandleAggregator(AbstractRegistry):
    """
    Aggregates TickEvents into OHLCV candles.

    Returns a CandleEvent when a bar closes, None while the bar is still building.
    Persistence is delegated to the injected AbstractCandleLogger so bar logic
    can be tested independently of DB state.
    Historical warmup is handled separately by HistoricalDataService.
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

    # ------------------------------------------------------------------
    # AbstractRegistry
    # ------------------------------------------------------------------

    async def handle(self, tick: TickEvent) -> CandleEvent | None:  # type: ignore[override]
        """
        Update the partial bar for this tick's instrument.

        Returns a CandleEvent if a bar just closed, None otherwise.
        """
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
    them through registered SignalGenerators so strategies are pre-seeded
    before live ticks arrive.

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
        self._algo_callbacks: list[SignalGenerator] = []

    def add_algo_registry(self, algo_registry: SignalGenerator) -> None:
        """Register a SignalGenerator to receive warmup candles during _setup."""
        self._algo_callbacks.append(algo_registry)

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

        for algo_reg in self._algo_callbacks:
            algo_reg.setup(candles_by_symbol)

        if all_candles and self._algo_callbacks:
            logger.info(
                "CandleAggregatorComponent: replaying %d warmup candles through %d algo registry(s)",  # noqa: E501
                len(all_candles),
                len(self._algo_callbacks),
            )
            for candle in all_candles:
                for algo_reg in self._algo_callbacks:
                    try:
                        await algo_reg.handle(candle)
                    except Exception:
                        logger.exception(
                            "CandleAggregatorComponent: warmup replay error for %s", candle.symbol
                        )

        logger.info("CandleAggregatorComponent: warm-up complete (%d candles)", len(all_candles))

    async def _run(self) -> None:
        await sleep_forever()
