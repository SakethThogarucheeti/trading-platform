from __future__ import annotations

from typing import Protocol

from trading.strategy.api.schemas import SignalEvent  # noqa: F401 — re-exported


class AbstractPositionStore(Protocol):
    async def get_position(self, symbol: str, instrument_type: str) -> object | None: ...


class AbstractTradingStore(Protocol):
    async def get_daily_realized_pnl(self, date: object) -> float: ...

    async def save_signal(self, event: object) -> None: ...


class AbstractAuditStore(Protocol):
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

    async def log_audit(self, module: str, level: str, message: str) -> None: ...


class CacherFactory(Protocol):
    def pnl(self) -> object: ...
