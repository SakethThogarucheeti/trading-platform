from __future__ import annotations

from typing import Protocol

from trading.execution.api.schemas import FillEvent
from trading.risk.api.schemas import ValidatedOrderEvent  # noqa: F401 — re-exported


class Broker(Protocol):
    """execution's view of the broker — only place_order is needed."""

    async def place_order(
        self,
        symbol: str,
        side: object,
        qty: int,
        order_type: object,
        limit_price: float | None = None,
        instrument_type: str = "EQUITY",
        tick_log_id: int = 0,
    ) -> str: ...


class AbstractTradingStore(Protocol):
    async def update_order_status(
        self, kite_order_id: str, status: object, avg_price: float = 0
    ) -> None: ...

    async def get_daily_realized_pnl(self, for_date: object) -> float: ...

    async def save_signal(self, event: object) -> object: ...


class AbstractPositionStore(Protocol):
    async def get_position(self, symbol: str, instrument_type: str) -> object | None: ...

    async def update_position(
        self, fill: FillEvent, side: object, symbol: str, instrument_type: str
    ) -> None: ...


class CacherFactory(Protocol):
    def pnl(self) -> object: ...

    def api(self) -> object: ...
