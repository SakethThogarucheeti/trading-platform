from __future__ import annotations

from typing import Protocol

from trading.candles.api.schemas import CandleEvent
from trading.tick_ingest.api.schemas import TickEvent  # noqa: F401 — re-exported for consumers


class AbstractCandleStore(Protocol):
    """Storage contract for candles — persist and retrieve OHLCV bars."""

    async def save_candles(self, rows: list[dict]) -> None: ...

    async def get_candles_since(self, symbol: str, interval: str, since: object) -> list[dict]: ...


class AbstractAuditStore(Protocol):
    """Audit contract for candles — logs bar-close decisions."""

    async def log_decision(
        self,
        step: str,
        symbol: str,
        tick_log_id: int,
        context: object,
        algo_name: str | None = None,
        signal_id: object | None = None,
        session_id: str | None = None,
    ) -> None: ...


class AbstractHistoricalSource(Protocol):
    """Data source for historical OHLCV bars (e.g. broker.get_ohlc)."""

    def get_ohlc(self, symbol: str, interval: str, start: object, end: object) -> object: ...


class AbstractCandleConsumer(Protocol):
    """Receives CandleEvents — implemented by SignalGenerator and similar downstream handlers."""

    def setup(self, candles_by_symbol: dict[str, list[CandleEvent]]) -> None: ...

    async def handle(self, candle: CandleEvent) -> object: ...
