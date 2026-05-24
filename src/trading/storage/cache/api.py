from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

from trading.storage.cache.base import BaseCacher
from trading.storage.cache.backend import ValueCache


class ApiResponseCacher(BaseCacher[str]):
    """
    TTL-based cache for dashboard API response bodies (JSON strings).

    Producers write the already-serialized JSON response string.
    Consumers read it back and return it directly — no re-serialization.

    Invalidation is always called via the factory to keep the call traceable:
        factory.api().invalidate_pnl(today)
    """

    def __init__(self, cache: ValueCache) -> None:
        super().__init__(cache)

    def make_key(self, *args: object) -> str:
        return "api:" + ":".join(str(a) for a in args)

    async def get_or_set_response(
        self,
        key_args: tuple,
        producer: Callable[[], Awaitable[str]],
        ttl: int,
    ) -> str:
        return await self.get_or_set(key_args, producer=producer, ttl=ttl)

    async def invalidate_pnl(self, for_date: date) -> None:
        """
        Called via factory.api().invalidate_pnl(today) after a fill is persisted.
        Explicit factory call makes the dependency on ApiResponseCacher visible at
        the call site (e.g. OrderExecutor.handle_fill) for easy tracing.
        """
        await self.invalidate("pnl", for_date.isoformat())
        await self.invalidate("pnl:by_algo", for_date.isoformat())
