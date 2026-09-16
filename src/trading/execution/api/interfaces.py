from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from trading.core.schemas import OrderStatus, OrderType, Side
from trading.execution.api.schemas import FillEvent
from trading.risk.api.schemas import ValidatedOrderEvent  # noqa: F401 — re-exported

# trading-platform#87: these Protocols previously declared several parameters as
# `object` intending it as a permissive "accepts anything" widening. That's
# backwards for a Protocol's parameter types, which are contravariant: an
# implementation's parameter type must be the SAME OR WIDER than the
# Protocol's declared type for pyright to accept it structurally. Declaring
# `object` (the widest possible type) requires every implementation to also
# accept literally any object, which none of them do (they all declare the
# same concrete type below) -- so `object` was actually the narrowest
# possible choice from the implementation's point of view, guaranteeing the
# mismatch this issue reports. The fix is to declare each parameter as the
# exact concrete type every real implementation already uses, which is
# side/order_type/status/for_date's true common type (confirmed via
# TradingStore/PositionStore/PaperBroker/ZerodhaBroker's own signatures) --
# not to widen it further.


class Broker(Protocol):
    """execution's view of the broker — only place_order is needed."""

    async def place_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        limit_price: float | None = None,
        instrument_type: str = "EQUITY",
        tick_log_id: int = 0,
        client_tag: str | None = None,
    ) -> str: ...


class AbstractTradingStore(Protocol):
    async def update_order_status(
        self, kite_order_id: str, status: OrderStatus, avg_price: float = 0
    ) -> bool: ...

    async def update_order_status_in_session(
        self, session: AsyncSession, kite_order_id: str, status: OrderStatus, avg_price: float = 0
    ) -> bool: ...

    async def get_daily_realized_pnl(self, for_date: date) -> float: ...

    async def save_signal(self, event: ValidatedOrderEvent) -> object: ...

    async def increment_pnl_aggregate(
        self,
        for_date: date,
        delta: float | Decimal,
        algo_name: str = "ALL",
        symbol: str = "ALL",
    ) -> None: ...

    async def increment_pnl_aggregate_in_session(
        self,
        session: AsyncSession,
        for_date: date,
        delta: float | Decimal,
        algo_name: str = "ALL",
        symbol: str = "ALL",
    ) -> None: ...

    async def get_filled_fills(
        self, for_date: date, symbol: str, exclude_kite_order_id: str | None = None
    ) -> list[tuple[str, int, Decimal]]: ...

    async def get_filled_fills_in_session(
        self,
        session: AsyncSession,
        for_date: date,
        symbol: str,
        exclude_kite_order_id: str | None = None,
    ) -> list[tuple[str, int, Decimal]]: ...

    async def get_order_algo_name_in_session(
        self, session: AsyncSession, kite_order_id: str
    ) -> str | None: ...

    def transaction(self) -> AbstractAsyncContextManager[AsyncSession]: ...


class AbstractPositionStore(Protocol):
    async def get_position(self, symbol: str, instrument_type: str) -> object | None: ...

    async def update_position(
        self, fill: FillEvent, side: Side, symbol: str, instrument_type: str, algo_name: str
    ) -> None: ...

    async def update_position_in_session(
        self,
        session: AsyncSession,
        fill: FillEvent,
        side: Side,
        symbol: str,
        instrument_type: str,
        algo_name: str,
    ) -> None: ...


class CacherFactory(Protocol):
    def api(self) -> object: ...
