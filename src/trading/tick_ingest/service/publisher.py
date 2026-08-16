from __future__ import annotations

import logging

from aiokafka import AIOKafkaProducer

from trading.app.faust_app import TICKS_TOPIC_NAME
from trading.tick_ingest.api.schemas import TickEvent

logger = logging.getLogger(__name__)


class TickPublisher:
    """
    Publishes validated TickEvents onto the Kafka ``ticks`` topic.

    Called by KiteIngestor after each tick is persisted. Owns the
    AIOKafkaProducer's lifecycle (start/stop) — call start()/stop() from
    KiteIngestor's _setup()/_teardown().

    Unlike the Redis pub/sub predecessor, a publish failure here is not
    silently swallowed: AIOKafkaProducer.send_and_wait raises on failure
    (including after its own internal retries), and that exception now
    propagates to the caller instead of only being logged, since it
    represents a tick that must not be dropped without KiteIngestor
    knowing about it.

    Circuit-breaker state (``circuit:state``) is unrelated to the tick data
    path and stays on Redis — it's a small cross-process flag polled by
    RedisCircuitBreaker.sync_loop, not part of the pipeline being migrated.
    """

    def __init__(self, producer: AIOKafkaProducer, redis: object) -> None:
        self._producer = producer
        self._redis = redis

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def publish(self, tick: TickEvent) -> None:
        key = str(tick.instrument_token).encode("utf-8")
        payload = tick.model_dump_json().encode("utf-8")
        await self._producer.send_and_wait(TICKS_TOPIC_NAME, value=payload, key=key)

    async def set_circuit_state(self, open: bool) -> None:
        if self._redis is None:
            return
        value = "open" if open else "closed"
        try:
            await self._redis.set("circuit:state", value)  # type: ignore[attr-defined]
        except Exception:
            logger.error(
                "TickPublisher: failed to set circuit:state=%s — workers may act on stale circuit state",
                value,
                exc_info=True,
            )
