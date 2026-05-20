from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from anyio import sleep_forever
from pydantic import BaseModel, Field

from trading.broker.base.broker import Broker
from trading.core.messaging import AbstractRegistry
from trading.core.models import Instrument
from trading.core.schemas import CandleEvent, InstrumentType, TickEvent
from trading.core.tasks import fire
from quantindicators.types import CandleRow
from trading.engine.bar_accumulator import INTERVAL_MINUTES, AbstractBarAccumulator, BarAccumulator, SymbolConfig
from trading.engine.component import Component
from trading.strategy.signal_generator import SignalGenerator
from trading.storage.stores.audit import AbstractAuditStore, AuditContext
from trading.storage.stores.candle import AbstractCandleDataStore

logger = logging.getLogger(__name__)

_CALENDAR_MINUTES_PER_TRADING_MINUTE = (7 / 5) * (1440 / 375)  # ≈ 5.4


@dataclass
class CandleEmittedContext(AuditContext):
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    candle_ts: str


def _ensure_utc(dt: object) -> datetime:
    if isinstance(dt, datetime):
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    raise TypeError(f"Expected datetime, got {type(dt)}")


class CandleConfig(BaseModel):
    """Configuration for the candle aggregation stage."""

    model_config = {"arbitrary_types_allowed": True}

    instruments: list[Instrument]
    intervals: list[str]
    warmup_count: int = Field(default=200, gt=0)


class CandleAggregator(AbstractRegistry):
    """
    Aggregates TickEvents into OHLCV candles.

    Returns a CandleEvent when a bar closes, None while the bar is still building.
    Emits warm-up candles during warmup() by fetching historical data from the broker.
    """

    def __init__(
        self,
        config: CandleConfig,
        broker: Broker,
        candle: AbstractCandleDataStore,
        audit: AbstractAuditStore,
        accumulator: AbstractBarAccumulator | None = None,
    ) -> None:
        self._config = config
        self._broker = broker
        self._candle = candle
        self._audit = audit

        self._symbols: list[SymbolConfig] = [
            SymbolConfig(
                symbol=inst.symbol,
                instrument_token=inst.token,
                instrument_type=InstrumentType(inst.instrument_type),
            )
            for inst in config.instruments
        ]
        self._token_sc: dict[int, SymbolConfig] = {sc.instrument_token: sc for sc in self._symbols}
        self._accumulator: AbstractBarAccumulator = accumulator if accumulator is not None else BarAccumulator()

    # ------------------------------------------------------------------
    # Warm-up — call once before the live tick stream starts
    # ------------------------------------------------------------------

    async def warmup(self) -> list[CandleEvent]:
        """Fetch historical candles and return them as CandleEvents (tick_log_id=0)."""
        events: list[CandleEvent] = []
        now = datetime.now(UTC)
        max_minutes = max(
            (INTERVAL_MINUTES.get(iv, 1) for iv in self._config.intervals), default=1
        )
        trading_minutes_needed = self._config.warmup_count * max_minutes
        calendar_minutes = trading_minutes_needed * _CALENDAR_MINUTES_PER_TRADING_MINUTE
        lookback_hours = int(calendar_minutes / 60) + 24
        start = now - timedelta(hours=lookback_hours)

        fetch_failures = parse_failures = persist_failures = 0

        for sc in self._symbols:
            for interval in self._config.intervals:
                try:
                    df = self._broker.get_ohlc(sc.symbol, interval, start, now)
                except Exception as exc:
                    logger.warning(
                        "CandleAggregator: warmup fetch failed for %s %s — %s", sc.symbol, interval, exc
                    )
                    fetch_failures += 1
                    continue
                if df.is_empty():
                    continue
                warmup_rows: list[CandleRow] = []
                for row in df.tail(self._config.warmup_count).iter_rows(named=True):
                    try:
                        ts = _ensure_utc(row["date"])
                        events.append(
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
                        warmup_rows.append(
                            CandleRow(
                                symbol=sc.symbol,
                                interval=interval,
                                ts=ts,
                                open=float(row["open"]),
                                high=float(row["high"]),
                                low=float(row["low"]),
                                close=float(row["close"]),
                                volume=int(row.get("volume", 0)),
                            )
                        )
                    except Exception as exc:
                        logger.warning(
                            "CandleAggregator: invalid warmup row %s %s — %s",
                            sc.symbol,
                            interval,
                            exc,
                        )
                        parse_failures += 1
                if warmup_rows:
                    try:
                        await self._candle.save_candles(warmup_rows)
                    except Exception as exc:
                        logger.warning(
                            "CandleAggregator: warmup persist failed for %s %s — %s",
                            sc.symbol,
                            interval,
                            exc,
                        )
                        persist_failures += 1

        if fetch_failures or parse_failures or persist_failures:
            logger.warning(
                "CandleAggregator: warmup finished with errors — fetch=%d parse=%d persist=%d",
                fetch_failures,
                parse_failures,
                persist_failures,
            )
        logger.info("CandleAggregator: warmup produced %d candles", len(events))
        return events

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
                fire(self._log_candle(candle))
                return candle

        return None

    async def _log_candle(self, event: CandleEvent) -> None:
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
                "CandleAggregator: candle persist/log failed for %s %s — %s: %s",
                event.symbol,
                event.interval,
                type(exc).__name__,
                exc,
            )

class CandleAggregatorComponent(Component):
    """
    Lifecycle component wrapping CandleAggregator.

    _setup runs the warm-up (fetches historical candles from the broker) and
    replays them through any registered algo registries so strategies are
    pre-seeded before live ticks arrive.

    _run sleeps forever — live ticks are fed via KiteIngestor's on_tick callbacks.
    """

    def __init__(self, candle_aggregator: CandleAggregator) -> None:
        super().__init__(name="candle_aggregator")
        self._aggregator = candle_aggregator
        self._algo_callbacks: list[SignalGenerator] = []

    def add_algo_registry(self, algo_registry: SignalGenerator) -> None:
        """Register an SignalGenerator to receive warmup candles during _setup."""
        self._algo_callbacks.append(algo_registry)

    async def _setup(self) -> None:
        warmup_candles = await self._aggregator.warmup()
        if warmup_candles and self._algo_callbacks:
            logger.info(
                "CandleAggregatorComponent: replaying %d warmup candles through %d algo registry(s)",
                len(warmup_candles),
                len(self._algo_callbacks),
            )
            for candle in warmup_candles:
                for algo_reg in self._algo_callbacks:
                    try:
                        await algo_reg.handle(candle)
                    except Exception:
                        logger.exception(
                            "CandleAggregatorComponent: warmup replay error for %s", candle.symbol
                        )
        logger.info("CandleAggregatorComponent: warm-up complete")

    async def _run(self) -> None:
        await sleep_forever()
