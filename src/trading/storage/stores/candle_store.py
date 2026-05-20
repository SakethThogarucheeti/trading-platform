"""CandleStore — Postgres-backed AbstractCandleStore with optional Redis caching."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from quantindicators.store import AbstractCandleStore
from quantindicators.types import CandleRow

from trading.storage.stores.candle import AbstractCandleDataStore

_log = logging.getLogger(__name__)

_CACHE_TTL = 90


@runtime_checkable
class RedisClientProtocol(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def setex(self, key: str, ttl: int, value: str) -> None: ...


class CandleStore(AbstractCandleStore):
    """
    Fetch candle rows from Postgres for indicator computation.

    When a Redis client is supplied, raw candle lists are cached keyed by
    ``(symbol, interval, limit)`` or ``(symbol, interval, since_iso)``.
    All indicator objects that need the same window share one cache entry,
    so only one DB round-trip occurs per bar per unique fetch signature.

    Redis is purely optional — when absent all reads go directly to Postgres.
    """

    def __init__(
        self,
        candle_store: AbstractCandleDataStore,
        redis: RedisClientProtocol | None = None,
    ) -> None:
        self._candle = candle_store
        self._redis = redis

    async def fetch(self, symbol: str, interval: str, limit: int) -> list[CandleRow]:
        """Return the last *limit* candles ordered ts ASC (oldest→newest)."""
        cache_key = f"cs:candles:{symbol}:{interval}:n{limit}"
        return await self._get_or_fetch(
            cache_key,
            lambda: self._candle.get_candles(symbol, interval, limit),
        )

    async def fetch_since(self, symbol: str, interval: str, since: datetime) -> list[CandleRow]:
        """Return all candles with ts >= *since*, ordered ts ASC."""
        cache_key = f"cs:candles:{symbol}:{interval}:since:{since.isoformat()}"
        return await self._get_or_fetch(
            cache_key,
            lambda: self._candle.get_candles_since(symbol, interval, since),
        )

    async def _get_or_fetch(
        self,
        key: str,
        query: Callable[[], Coroutine[Any, Any, list[CandleRow]]],
    ) -> list[CandleRow]:
        if self._redis is not None:
            try:
                cached = await self._redis.get(key)
                if cached is not None:
                    return json.loads(cached)  # type: ignore[no-any-return]
            except Exception as exc:
                _log.debug("CandleStore: Redis get failed for %r — %s", key, exc)

        rows: list[CandleRow] = await query()

        if self._redis is not None and rows:
            try:
                await self._redis.setex(key, _CACHE_TTL, json.dumps(rows, default=str))
            except Exception as exc:
                _log.debug("CandleStore: Redis set failed for %r — %s", key, exc)

        return rows
