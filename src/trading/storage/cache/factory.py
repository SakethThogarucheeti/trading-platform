from __future__ import annotations

from trading.storage.cache.api import ApiResponseCacher
from trading.storage.cache.backend import ValueCache
from trading.storage.cache.pnl import PnlCacher
from trading.storage.cache.rolling_state import RollingStateCacher


class CacherFactory:
    """
    Creates and caches all typed cachers. DI injects this factory as a single
    APP-scoped singleton — consumers call factory.pnl(), factory.rolling_state(),
    or factory.api() to obtain the appropriate cacher.

    Cacher instances are lazily created and reused (one per factory instance).
    """

    def __init__(self, cache: ValueCache) -> None:
        self._cache = cache
        self._pnl: PnlCacher | None = None
        self._rolling_state: RollingStateCacher | None = None
        self._api: ApiResponseCacher | None = None

    def pnl(self) -> PnlCacher:
        if self._pnl is None:
            self._pnl = PnlCacher(self._cache)
        return self._pnl

    def rolling_state(self) -> RollingStateCacher:
        if self._rolling_state is None:
            self._rolling_state = RollingStateCacher(self._cache)
        return self._rolling_state

    def api(self) -> ApiResponseCacher:
        if self._api is None:
            self._api = ApiResponseCacher(self._cache)
        return self._api
