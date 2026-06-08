from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trading.candles.api.schemas import CandleEvent  # noqa: F401 — re-exported


class AbstractCandleStore(Protocol):
    """strategies' view of candle history (same shape as quantindicators.store.AbstractCandleStore)."""

    async def fetch(self, symbol: str, interval: str, limit: int) -> list: ...

    async def fetch_since(self, symbol: str, interval: str, since: datetime) -> list: ...


class AbstractChartStore(Protocol):
    """Receives indicator values from strategies for dashboard display."""

    async def log_indicator(
        self,
        algo_name: str,
        symbol: str,
        interval: str,
        chart: str,
        series: str,
        ts: datetime,
        value: float,
        session_id: str | None = None,
    ) -> None: ...


class AbstractConfigStore(Protocol):
    """Reads and writes algo configuration and runtime state."""

    async def upsert_algo_state(self, algo_name: str, state: dict) -> None: ...

    async def get_algo_state(self, algo_name: str) -> dict | None: ...


class AbstractAuditStore(Protocol):
    """Audit contract for strategy — logs signal generation decisions."""

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


class CacherFactory(Protocol):
    """Provides named cache instances for rolling state persistence."""

    def rolling_state(self) -> object: ...
