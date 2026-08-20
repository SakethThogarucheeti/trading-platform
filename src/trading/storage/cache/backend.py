from __future__ import annotations

import json
import logging
import time
from typing import Any

from cashews import Cache

_log = logging.getLogger(__name__)

_backend = Cache()


class ValueCache:
    """
    Two-tier cache: in-memory dict (always written, sync-accessible) + cashews (async Redis).

    The in-memory dict is the source of truth for sync callers (e.g. on_fill).
    cashews provides async Redis persistence so values survive process restarts.
    On async get(), memory is checked first; Redis is consulted only on a miss,
    and the result is stored back into memory so subsequent sync reads are fast.
    """

    def __init__(self) -> None:
        # raw JSON string + absolute expiry (time.monotonic() seconds, or None
        # for no expiry), always in sync with latest writes.
        self._mem: dict[str, tuple[str, float | None]] = {}

    def _mem_get(self, key: str) -> str | None:
        entry = self._mem.get(key)
        if entry is None:
            return None
        raw, expires_at = entry
        if expires_at is not None and time.monotonic() >= expires_at:
            # A TTL passed to set()/get_or_set() was previously only ever
            # honored by the Redis tier — this in-memory tier held every
            # value forever regardless of ttl, so callers relying on TTL
            # alone to pick up fresh data (e.g. /api/pnl) never did within a
            # warm process. Expire it here too so TTL is a real guarantee.
            del self._mem[key]
            return None
        return raw

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        raw = self._mem_get(key)
        if raw is None:
            try:
                raw = await _backend.get(key)  # type: ignore[reportUnknownMemberType]
                if raw is not None:
                    # Mirror Redis's own remaining TTL locally rather than
                    # caching forever — otherwise a cross-process cache hit
                    # would reintroduce the same never-expires bug.
                    ttl = await _backend.get_expire(key)  # type: ignore[reportUnknownMemberType]
                    expires_at = time.monotonic() + ttl if ttl and ttl > 0 else None
                    self._mem[key] = (raw, expires_at)
            except Exception as exc:
                _log.debug("ValueCache.get Redis error key=%r: %s", key, exc)
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        raw = json.dumps(value)
        expires_at = time.monotonic() + ttl if ttl is not None else None
        self._mem[key] = (raw, expires_at)
        try:
            await _backend.set(key, raw, expire=ttl)  # type: ignore[reportUnknownMemberType]
        except Exception as exc:
            _log.debug("ValueCache.set Redis error key=%r: %s", key, exc)

    async def delete(self, key: str) -> None:
        self._mem.pop(key, None)
        try:
            await _backend.delete(key)  # type: ignore[reportUnknownMemberType]
        except Exception as exc:
            _log.debug("ValueCache.delete Redis error key=%r: %s", key, exc)

    # ------------------------------------------------------------------
    # Sync API (for handle_fill which is synchronous)
    # ------------------------------------------------------------------

    def get_sync(self, key: str) -> Any | None:
        raw = self._mem_get(key)
        return json.loads(raw) if raw is not None else None

    def set_sync(self, key: str, value: Any) -> None:
        """Write to in-memory only (no expiry). Redis persistence deferred to next async set()."""
        self._mem[key] = (json.dumps(value), None)


def setup_cache(redis_url: str | None) -> None:
    if redis_url:
        try:
            _backend.setup(redis_url)
            _log.info("ValueCache: Redis backend configured at %s", redis_url)
        except Exception as exc:
            _log.warning("ValueCache: Redis setup failed (%s) — falling back to memory", exc)
            _backend.setup("mem://")
    else:
        _backend.setup("mem://")
        _log.info("ValueCache: using in-memory backend (no REDIS_URL)")
