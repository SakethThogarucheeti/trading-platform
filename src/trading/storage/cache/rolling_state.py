from __future__ import annotations

from trading.storage.cache.base import BaseCacher
from trading.storage.cache.backend import ValueCache

_WINDOW_SIZE = 50


class RollingStateCacher(BaseCacher[dict]):  # type: ignore[type-arg]
    """
    Caches per-tick rolling strategy state (previous indicator values, bar counters).

    Key schema:
      state:{symbol}:{interval}:{tick_log_id}  →  rolling state dict for that bar
      win:{algo}:{symbol}:{interval}            →  list of the last 50 tick_log_ids

    Producers (SignalGenerator) call save() after every on_candle().
    Consumers (restore_state at startup) call load_latest() to restore rolling state.

    On invalid state (restore_from_state returns False), the caller invokes clear()
    to wipe the window and all referenced state entries. The cacher does NOT trigger
    warmup — that responsibility stays with SignalGenerator.setup().
    """

    def __init__(self, cache: ValueCache) -> None:
        super().__init__(cache)

    def make_key(self, symbol: str, interval: str, tick_log_id: int) -> str:  # type: ignore[override]
        return f"state:{symbol}:{interval}:{tick_log_id}"

    def _win_key(self, algo: str, symbol: str, interval: str) -> str:
        return f"win:{algo}:{symbol}:{interval}"

    async def save(
        self,
        algo: str,
        symbol: str,
        interval: str,
        tick_log_id: int,
        data: dict,  # type: ignore[type-arg]
    ) -> None:
        """Write the state snapshot and update the sliding window index."""
        state_key = self.make_key(symbol, interval, tick_log_id)
        win_key = self._win_key(algo, symbol, interval)

        await self._cache.set(state_key, data)

        raw_win = await self._cache.get(win_key)
        window: list[int] = raw_win if isinstance(raw_win, list) else []
        window.append(tick_log_id)
        if len(window) > _WINDOW_SIZE:
            window = window[-_WINDOW_SIZE:]
        await self._cache.set(win_key, window)

    async def load_latest(
        self, algo: str, symbol: str, interval: str
    ) -> tuple[int, dict] | None:  # type: ignore[type-arg]
        """Return (tick_log_id, state_dict) for the most recent bar, or None on miss."""
        window = await self._cache.get(self._win_key(algo, symbol, interval))
        if not window:
            return None
        latest_id: int = window[-1]
        state = await self._cache.get(self.make_key(symbol, interval, latest_id))
        return (latest_id, state) if state is not None else None

    async def clear(self, algo: str, symbol: str, interval: str) -> None:
        """
        Clear the window index and all state entries it references.
        Called when restore_from_state() returns False (corrupt/unusable state).
        Does NOT trigger warmup — caller falls back to the existing DB warmup path.
        """
        win_key = self._win_key(algo, symbol, interval)
        window = await self._cache.get(win_key)
        if window:
            for tick_id in window:
                await self._cache.delete(self.make_key(symbol, interval, tick_id))
        await self._cache.delete(win_key)
