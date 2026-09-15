from __future__ import annotations

from datetime import date
from typing import Protocol


class AbstractTradingStore(Protocol):
    async def get_daily_realized_pnl(self, for_date: date) -> float: ...


class AbstractPositionStore(Protocol):
    async def get_position(self, symbol: str, instrument_type: str) -> object | None: ...
