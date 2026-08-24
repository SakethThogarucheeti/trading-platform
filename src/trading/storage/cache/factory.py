from __future__ import annotations

from trading.core.clock import Clock
from trading.storage.cache.api import ApiResponseCacher
from trading.storage.cache.backend import ValueCache
from trading.storage.cache.rolling_state import RollingStateCacher


class CacherFactory:
    """
    Creates and caches all typed cachers. DI injects this factory as a single
    APP-scoped singleton — consumers call factory.rolling_state() or
    factory.api() to obtain the appropriate cacher.

    Cacher instances are lazily created and reused (one per factory instance).

    PnL is no longer cached here — it moved to a Postgres-backed running
    total (TradingStore.increment_pnl_aggregate/get_pnl_aggregate) so it's
    correct across concurrent worker processes; see execution/storage/store.py.
    """

    def __init__(self, cache: ValueCache, clock: Clock) -> None:
        self._cache = cache
        self._clock = clock
        self._rolling_state: RollingStateCacher | None = None
        self._api: ApiResponseCacher | None = None

    def rolling_state(self) -> RollingStateCacher:
        if self._rolling_state is None:
            self._rolling_state = RollingStateCacher(self._cache)
        return self._rolling_state

    def api(self) -> ApiResponseCacher:
        if self._api is None:
            self._api = ApiResponseCacher(self._cache)
        return self._api
