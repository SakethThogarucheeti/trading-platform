from __future__ import annotations

from datetime import datetime
from typing import cast

from trading.storage.cache.base import BaseCacher, KVCache

_WINDOW_SIZE = 50


class RollingStateCacher(BaseCacher[dict[str, object]]):
    """
    Caches per-tick rolling strategy state (previous indicator values, bar counters).

    Key schema:
      state:{symbol}:{interval}:{tick_log_id}  →  rolling state dict for that bar
      win:{algo}:{symbol}:{interval}            →  list of the last 50 tick_log_ids
      savedat:{algo}:{symbol}:{interval}        →  ISO timestamp of the latest save()

    Producers (SignalGenerator) call save() after every on_candle().
    Consumers (restore_state at startup) call load_latest() to restore rolling state.

    On invalid state (restore_from_state returns False), the caller invokes clear()
    to wipe the window and all referenced state entries. The cacher does NOT trigger
    warmup — that responsibility stays with SignalGenerator.setup().

    trading-platform#79: load_latest() refuses to return state saved on a
    different calendar day than the `now` it's given, or with no recorded
    save time at all (e.g. a cache row written before this check existed) —
    restoring genuinely cross-day-stale intraday indicator state could be
    worse than a clean warmup reseed, and there's no finer-grained
    trading-session boundary marker to check against. When in doubt, the
    caller falls back to warmup, same as an ordinary cache miss.
    """

    def __init__(self, cache: KVCache) -> None:
        super().__init__(cache)

    def make_key(self, symbol: str, interval: str, tick_log_id: int) -> str:  # type: ignore[override]
        return f"state:{symbol}:{interval}:{tick_log_id}"

    def _win_key(self, algo: str, symbol: str, interval: str) -> str:
        return f"win:{algo}:{symbol}:{interval}"

    def _saved_at_key(self, algo: str, symbol: str, interval: str) -> str:
        return f"savedat:{algo}:{symbol}:{interval}"

    async def save(
        self,
        algo: str,
        symbol: str,
        interval: str,
        tick_log_id: int,
        data: dict[str, object],
        saved_at: datetime,
    ) -> None:
        """Write the state snapshot, update the sliding window index, and stamp the save time."""
        state_key = self.make_key(symbol, interval, tick_log_id)
        win_key = self._win_key(algo, symbol, interval)

        await self._cache.set(state_key, data)

        raw_win = await self._cache.get(win_key)  # type: ignore[reportUnknownMemberType]
        window: list[int] = cast(list[int], raw_win) if isinstance(raw_win, list) else []
        window.append(tick_log_id)
        if len(window) > _WINDOW_SIZE:
            window = window[-_WINDOW_SIZE:]
        await self._cache.set(win_key, window)
        await self._cache.set(self._saved_at_key(algo, symbol, interval), saved_at.isoformat())

    async def load_latest(
        self, algo: str, symbol: str, interval: str, now: datetime
    ) -> tuple[int, dict[str, object]] | None:
        """
        Return (tick_log_id, state_dict) for the most recent bar, or None on
        a cache miss or a failed same-day staleness check (see class docstring).
        """
        saved_at_raw = await self._cache.get(self._saved_at_key(algo, symbol, interval))  # type: ignore[reportUnknownMemberType]
        if saved_at_raw is None or datetime.fromisoformat(saved_at_raw).date() != now.date():
            return None
        window = await self._cache.get(self._win_key(algo, symbol, interval))  # type: ignore[reportUnknownMemberType]
        if not window:
            return None
        latest_id: int = window[-1]
        state = await self._cache.get(self.make_key(symbol, interval, latest_id))  # type: ignore[reportUnknownMemberType]
        return (latest_id, state) if state is not None else None

    async def clear(self, algo: str, symbol: str, interval: str) -> None:
        """
        Clear the window index, the save timestamp, and all state entries
        the window references. Called when restore_from_state() returns
        False (corrupt/unusable state). Does NOT trigger warmup — caller
        falls back to the existing DB warmup path.
        """
        win_key = self._win_key(algo, symbol, interval)
        window = await self._cache.get(win_key)
        if window:
            for tick_id in window:
                await self._cache.delete(self.make_key(symbol, interval, tick_id))
        await self._cache.delete(win_key)
        await self._cache.delete(self._saved_at_key(algo, symbol, interval))
