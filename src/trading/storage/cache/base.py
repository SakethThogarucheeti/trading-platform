from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

from trading.storage.cache.backend import ValueCache

T = TypeVar("T")


class BaseCacher(ABC, Generic[T]):
    """
    Abstract base for a typed cache domain.

    Subclasses define:
      make_key(*args) -> str          key schema for this domain
      default_ttl() -> int | None     TTL in seconds (None = no expiry)

    Core pattern — cache-aside with producer callback:
      value = await cacher.get_or_set(
          (today,),
          producer=lambda: store.get_daily_realized_pnl(today),
      )
    The producer is called only on a cache miss; its result is cached and returned.
    """

    def __init__(self, cache: ValueCache) -> None:
        self._cache = cache

    @abstractmethod
    def make_key(self, *args: object) -> str:
        """Return the cache key for the given identity arguments."""

    def default_ttl(self) -> int | None:
        return None

    async def get_or_set(
        self,
        key_args: tuple,
        producer: Callable[[], Awaitable[T]],
        ttl: int | None = None,
    ) -> T:
        key = self.make_key(*key_args)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        value = await producer()
        await self._cache.set(key, value, ttl=ttl if ttl is not None else self.default_ttl())
        return value

    async def invalidate(self, *key_args: object) -> None:
        await self._cache.delete(self.make_key(*key_args))
