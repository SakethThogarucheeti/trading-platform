"""CandleStore — Postgres-backed AbstractCandleStore."""

from __future__ import annotations

from datetime import datetime

from quantindicators.store import AbstractCandleStore
from quantindicators.types import CandleRow

from trading.candles.api.interfaces import AbstractCandleStore as AbstractCandleDataStore


class CandleStore(AbstractCandleStore):
    """Fetch candle rows from Postgres for indicator computation."""

    def __init__(self, candle_store: AbstractCandleDataStore) -> None:
        self._candle = candle_store

    async def fetch(self, symbol: str, interval: str, limit: int) -> list[CandleRow]:
        """Return the last *limit* candles ordered ts ASC (oldest→newest)."""
        return await self._candle.get_candles(symbol, interval, limit)

    async def fetch_since(self, symbol: str, interval: str, since: datetime) -> list[CandleRow]:
        """Return all candles with ts >= *since*, ordered ts ASC."""
        return await self._candle.get_candles_since(symbol, interval, since)
