from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from trading.candles.api.interfaces import AbstractAuditStore, AbstractCandleStore
from trading.candles.api.schemas import CandleEvent
from trading.core.models import Instrument

logger = logging.getLogger(__name__)


class CandleConfig(BaseModel):
    """Configuration for the candle aggregation stage."""

    model_config = {"arbitrary_types_allowed": True}

    instruments: list[Instrument]
    intervals: list[str]
    warmup_count: int = Field(default=200, gt=0)


@dataclass
class CandleEmittedContext:
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    candle_ts: str


class AbstractCandleLogger(Protocol):
    """Persist a closed candle and write the audit decision log entry."""

    async def log(self, event: CandleEvent) -> None: ...


class CandlePersister:
    """
    Concrete implementation of AbstractCandleLogger.

    Saves the candle row to the DB and, when tick_log_id is non-zero,
    writes a CANDLE_EMITTED entry to the audit decision log.
    """

    def __init__(self, candle: AbstractCandleStore, audit: AbstractAuditStore) -> None:
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
