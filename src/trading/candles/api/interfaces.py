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

    def symbols_needing_rewarm(self) -> set[str]:
        """Symbols this consumer has not yet processed a live tick for.

        Used to gate a post-login re-warm (trading-platform#40) so it only
        re-seeds strategy state for symbols that missed startup warmup —
        never a symbol already advanced by a live tick.
        """
        ...

    def rewarm(self, candles_by_symbol: dict[str, list[CandleEvent]]) -> None:
        """Re-seed only the symbols in `candles_by_symbol` still needing a re-warm."""
        ...

    async def restore_state(self, symbols: set[str] | None = None) -> None:
        """
        Restore rolling strategy state from the durable cache for `symbols`
        (or every configured symbol, if None), overriding the warmup seed
        wherever a valid, same-day cache entry exists (trading-platform#79).
        """
        ...
