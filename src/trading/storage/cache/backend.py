from __future__ import annotations

import json
import logging
import time
from typing import Any

_log = logging.getLogger(__name__)


class ValueCache:
    """In-memory key/value cache with per-key TTL."""

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
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        raw = json.dumps(value)
        expires_at = time.monotonic() + ttl if ttl is not None else None
        self._mem[key] = (raw, expires_at)

    async def delete(self, key: str) -> None:
        self._mem.pop(key, None)

    # ------------------------------------------------------------------
    # Sync API (for handle_fill which is synchronous)
    # ------------------------------------------------------------------

    def get_sync(self, key: str) -> Any | None:
        raw = self._mem_get(key)
        return json.loads(raw) if raw is not None else None

    def set_sync(self, key: str, value: Any) -> None:
        """Write to in-memory only (no expiry)."""
        self._mem[key] = (json.dumps(value), None)
