from __future__ import annotations

import json
import logging
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
        self._mem: dict[str, str] = {}  # raw JSON strings, always in sync with latest writes

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        raw = self._mem.get(key)
        if raw is None:
            try:
                raw = await _backend.get(key)  # type: ignore[reportUnknownMemberType]
                if raw is not None:
                    self._mem[key] = raw  # populate memory from Redis on first read
            except Exception as exc:
                _log.debug("ValueCache.get Redis error key=%r: %s", key, exc)
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        raw = json.dumps(value)
        self._mem[key] = raw
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
        raw = self._mem.get(key)
        return json.loads(raw) if raw is not None else None

    def set_sync(self, key: str, value: Any) -> None:
        """Write to in-memory only. Redis persistence deferred to next async set()."""
        self._mem[key] = json.dumps(value)


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
