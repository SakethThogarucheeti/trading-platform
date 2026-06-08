from __future__ import annotations

import logging

from trading.tick_ingest.api.schemas import TickEvent

logger = logging.getLogger(__name__)


class TickPublisher:
    """
    Publishes validated TickEvents to Redis pub/sub channels.

    Called by KiteIngestor after each tick is persisted. Stateless helper —
    not a Component; lifecycle is owned by KiteIngestor.

    Channel scheme: ``ticks:<instrument_token>``
    Circuit state key: ``circuit:state`` (value: ``"open"`` | ``"closed"``)
    """

    def __init__(self, redis: object) -> None:
        self._redis = redis

    async def publish(self, tick: TickEvent) -> None:
        channel = f"ticks:{tick.instrument_token}"
        payload = tick.model_dump_json()
        try:
            await self._redis.publish(channel, payload)  # type: ignore[attr-defined]
        except Exception:
            logger.debug("TickPublisher: publish failed for token %s", tick.instrument_token)

    async def set_circuit_state(self, open: bool) -> None:
        value = "open" if open else "closed"
        try:
            await self._redis.set("circuit:state", value)  # type: ignore[attr-defined]
        except Exception:
            logger.debug("TickPublisher: failed to set circuit:state=%s", value)
