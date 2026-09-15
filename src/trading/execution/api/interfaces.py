from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

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
        client_tag: str | None = None,
    ) -> str: ...


class AbstractTradingStore(Protocol):
    async def update_order_status(
        self, kite_order_id: str, status: object, avg_price: float = 0
    ) -> bool: ...

    async def update_order_status_in_session(
        self, session: AsyncSession, kite_order_id: str, status: object, avg_price: float = 0
    ) -> bool: ...

    async def get_daily_realized_pnl(self, for_date: object) -> float: ...

    async def save_signal(self, event: object) -> object: ...

    async def increment_pnl_aggregate(
        self, for_date: object, delta: float, algo_name: str = "ALL", symbol: str = "ALL"
    ) -> None: ...

    async def increment_pnl_aggregate_in_session(
        self,
        session: AsyncSession,
        for_date: object,
        delta: float,
        algo_name: str = "ALL",
        symbol: str = "ALL",
    ) -> None: ...

    async def get_filled_fills(
        self, for_date: object, symbol: str, exclude_kite_order_id: str | None = None
    ) -> list[tuple[str, int, float]]: ...

    async def get_filled_fills_in_session(
        self,
        session: AsyncSession,
        for_date: object,
        symbol: str,
        exclude_kite_order_id: str | None = None,
    ) -> list[tuple[str, int, float]]: ...

    def transaction(self) -> AbstractAsyncContextManager[AsyncSession]: ...


class AbstractPositionStore(Protocol):
    async def get_position(self, symbol: str, instrument_type: str) -> object | None: ...

    async def update_position(
        self, fill: FillEvent, side: object, symbol: str, instrument_type: str
    ) -> None: ...

    async def update_position_in_session(
        self,
        session: AsyncSession,
        fill: FillEvent,
        side: object,
        symbol: str,
        instrument_type: str,
    ) -> None: ...


class CacherFactory(Protocol):
    def api(self) -> object: ...
