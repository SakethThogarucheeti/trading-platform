"""DI provider for the indicator library."""

from __future__ import annotations

from trading.candles.api.interfaces import AbstractCandleStore as AbstractCandleDataStore
from trading.storage.stores.candle_store import CandleStore, RedisClientProtocol


def make_candle_store(
    candle_store: AbstractCandleDataStore,
    redis: RedisClientProtocol | None = None,
) -> CandleStore:
    """Build the shared CandleStore, wiring in Redis when configured."""
    return CandleStore(candle_store=candle_store, redis=redis)
