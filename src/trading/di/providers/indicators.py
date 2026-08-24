"""DI provider for the indicator library."""

from __future__ import annotations

from trading.candles.api.interfaces import AbstractCandleStore as AbstractCandleDataStore
from trading.storage.stores.candle_store import CandleStore


def make_candle_store(candle_store: AbstractCandleDataStore) -> CandleStore:
    """Build the shared CandleStore."""
    return CandleStore(candle_store=candle_store)
