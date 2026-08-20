"""CandleStore — Postgres-backed AbstractCandleStore with optional Redis caching."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from quantindicators.store import AbstractCandleStore
from quantindicators.types import CandleRow

from trading.candles.api.interfaces import AbstractCandleStore as AbstractCandleDataStore

_log = logging.getLogger(__name__)

_CACHE_TTL = 90

# Seconds per bar, by the interval strings used throughout this codebase
# (see /api/settings candle_intervals). Used to cap the cache TTL below one
# bar's duration — see _cache_ttl_for.
_BAR_SECONDS = {
    "1min": 60,
    "3min": 180,
    "5min": 300,
    "10min": 600,
    "15min": 900,
    "30min": 1800,
    "60min": 3600,
}


def _cache_ttl_for(interval: str) -> int:
    """
    Cap the cache TTL below one bar's duration for this interval.

    A flat 90s TTL with no invalidation-on-write meant a "last N candles"
    fetch cached right after one bar closed was often still "fresh" when the
    NEXT bar closed a minute later (90s > 60s bar spacing) — every indicator
    built on this store (EMA, RSI, ATR, linreg, DPO, squeeze, ...) would
    silently compute over a window missing the just-closed candle on
    whichever calls landed inside that stale window, roughly half the time
    at 1min granularity. Unknown/custom intervals fall back to the original
    flat ceiling.
    """
    bar_secs = _BAR_SECONDS.get(interval)
    if bar_secs is None:
        return _CACHE_TTL
    return max(1, min(_CACHE_TTL, bar_secs - 5))


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
            ttl=_cache_ttl_for(interval),
        )

    async def fetch_since(self, symbol: str, interval: str, since: datetime) -> list[CandleRow]:
        """Return all candles with ts >= *since*, ordered ts ASC."""
        cache_key = f"cs:candles:{symbol}:{interval}:since:{since.isoformat()}"
        return await self._get_or_fetch(
            cache_key,
            lambda: self._candle.get_candles_since(symbol, interval, since),
            ttl=_cache_ttl_for(interval),
        )

    async def _get_or_fetch(
        self,
        key: str,
        query: Callable[[], Coroutine[Any, Any, list[CandleRow]]],
        ttl: int,
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
                await self._redis.setex(key, ttl, json.dumps(rows, default=str))
            except Exception as exc:
                _log.debug("CandleStore: Redis set failed for %r — %s", key, exc)

        return rows
