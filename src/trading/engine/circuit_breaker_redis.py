from __future__ import annotations

import logging

from anyio import sleep

from trading.engine.tick_ingestor import CircuitBreaker

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 2.0


class RedisCircuitBreaker(CircuitBreaker):
    """
    Drop-in replacement for CircuitBreaker in worker processes.

    State is cached locally; ``sync_loop()`` must be run as a background
    coroutine (e.g. via anyio task group) to keep the cache fresh.
    Workers should never call ``open()`` or ``close()`` directly — state is
    owned by the ingestor process and propagated via Redis.
    """

    def __init__(self, redis: object, poll_interval_secs: float = _DEFAULT_POLL_INTERVAL) -> None:
        super().__init__()
        self._redis = redis
        self._poll_interval = poll_interval_secs

    async def sync_loop(self) -> None:
        """Poll ``circuit:state`` from Redis and update the cached bool."""
        while True:
            try:
                val = await self._redis.get("circuit:state")  # type: ignore[attr-defined]
                self._open = val == b"open"
            except Exception:
                pass  # keep last known state on Redis error
            await sleep(self._poll_interval)
